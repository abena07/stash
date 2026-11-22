import { App, Notice, Plugin, PluginSettingTab, Setting, TFile, normalizePath, requestUrl } from "obsidian";
import { Action, RemoteFile, State, nextState, plan, summary } from "./sync.ts";

// the public stash bot. people who self-host change this in settings.
const DEFAULT_SERVER = "https://api.stash.bennett-eghan.com";

interface Settings {
  server: string;
  code: string;
  folder: string;
  which: "read" | "loved" | "all";
  intervalMinutes: number;
}

const DEFAULTS: Settings = {
  server: DEFAULT_SERVER,
  code: "",
  folder: "stash",
  which: "read",
  intervalMinutes: 30,
};

interface Saved {
  settings: Settings;
  state: State;
}

export default class StashPlugin extends Plugin {
  settings: Settings = { ...DEFAULTS };
  state: State = {};
  private timer: number | null = null;
  private syncing = false;

  async onload() {
    const saved = ((await this.loadData()) ?? {}) as Partial<Saved>;
    this.settings = { ...DEFAULTS, ...(saved.settings ?? {}) };
    this.state = saved.state ?? {};

    this.addCommand({ id: "sync-now", name: "Sync now", callback: () => this.sync(true) });
    this.addSettingTab(new StashSettings(this.app, this));
    this.app.workspace.onLayoutReady(() => this.sync(false));
    this.schedule();
  }

  async persist() {
    await this.saveData({ settings: this.settings, state: this.state } satisfies Saved);
  }

  schedule() {
    if (this.timer !== null) window.clearInterval(this.timer);
    const minutes = Math.max(5, this.settings.intervalMinutes);
    this.timer = window.setInterval(() => this.sync(false), minutes * 60 * 1000);
    this.registerInterval(this.timer);
  }

  async sync(loud: boolean) {
    if (this.syncing) return;
    if (!this.settings.code) {
      if (loud) new Notice("stash: add your code in settings first. get one with /connect obsidian in discord.");
      return;
    }
    this.syncing = true;
    try {
      const files = await this.fetchFiles();
      const vault = this.app.vault;
      const read = async (path: string) => {
        const file = vault.getAbstractFileByPath(normalizePath(path));
        return file instanceof TFile ? await vault.read(file) : null;
      };
      const actions = await plan(files, this.state, this.settings.folder, read);
      await this.apply(actions);
      this.state = nextState(this.state, actions);
      await this.persist();
      const changed = actions.some((a) => !["unchanged"].includes(a.kind));
      if (loud || changed) new Notice(summary(actions));
    } catch (err) {
      console.error("stash sync failed", err);
      if (loud) new Notice(`stash: ${err instanceof Error ? err.message : "sync failed"}`);
    } finally {
      this.syncing = false;
    }
  }

  private async fetchFiles(): Promise<RemoteFile[]> {
    const base = this.settings.server.replace(/\/+$/, "");
    const response = await requestUrl({
      url: `${base}/api/v1/links?which=${encodeURIComponent(this.settings.which)}`,
      headers: { Authorization: `Bearer ${this.settings.code.trim()}` },
      throw: false,
    });
    if (response.status === 401) throw new Error("that code doesn't work. get a new one with /connect obsidian.");
    if (response.status === 429) throw new Error("syncing too often. try again in a minute.");
    if (response.status !== 200) throw new Error(`the server answered ${response.status}.`);
    return (response.json as { files: RemoteFile[] }).files;
  }

  private async apply(actions: Action[]) {
    const vault = this.app.vault;
    const folder = normalizePath(this.settings.folder);
    if (folder && folder !== "/" && !vault.getAbstractFileByPath(folder)) {
      await vault.createFolder(folder);
    }
    for (const a of actions) {
      if (a.kind === "create") {
        await vault.create(normalizePath(a.path), a.content);
      } else if (a.kind === "update") {
        const file = vault.getAbstractFileByPath(normalizePath(a.path));
        if (file instanceof TFile) await vault.modify(file, a.content);
      } else if (a.kind === "rename") {
        const file = vault.getAbstractFileByPath(normalizePath(a.from));
        if (file instanceof TFile) {
          await this.app.fileManager.renameFile(file, normalizePath(a.to)); // keeps links to it working
          const moved = vault.getAbstractFileByPath(normalizePath(a.to));
          if (moved instanceof TFile) await vault.modify(moved, a.content);
        }
      }
    }
  }
}

class StashSettings extends PluginSettingTab {
  constructor(app: App, private plugin: StashPlugin) {
    super(app, plugin);
  }

  display(): void {
    const { containerEl } = this;
    const s = this.plugin.settings;
    containerEl.empty();

    containerEl.createEl("p", {
      text: "stash syncs the links you save with the stash discord bot into a folder here. "
        + "files you edit are never overwritten.",
    });

    new Setting(containerEl)
      .setName("Code")
      .setDesc("run /connect obsidian in discord and paste the code here. it works like a password.")
      .addText((t) => {
        t.inputEl.type = "password";
        t.setPlaceholder("stash_…").setValue(s.code).onChange(async (v) => {
          s.code = v.trim();
          await this.plugin.persist();
        });
      });

    new Setting(containerEl)
      .setName("Folder")
      .setDesc("where the files go in your vault.")
      .addText((t) => t.setValue(s.folder).onChange(async (v) => {
        s.folder = v.trim() || "stash";
        await this.plugin.persist();
      }));

    new Setting(containerEl)
      .setName("Which links")
      .addDropdown((d) => d
        .addOption("read", "read (marked ✅)")
        .addOption("loved", "loved (marked 👍)")
        .addOption("all", "all saved links")
        .setValue(s.which)
        .onChange(async (v) => {
          s.which = v as Settings["which"];
          await this.plugin.persist();
        }));

    new Setting(containerEl)
      .setName("Sync every")
      .setDesc("minutes, while obsidian is open (at least 5).")
      .addText((t) => t.setValue(String(s.intervalMinutes)).onChange(async (v) => {
        const n = Number.parseInt(v, 10);
        if (Number.isFinite(n) && n >= 5) {
          s.intervalMinutes = n;
          await this.plugin.persist();
          this.plugin.schedule();
        }
      }));

    new Setting(containerEl)
      .setName("Server")
      .setDesc("leave as is unless you run your own copy of stash.")
      .addText((t) => t.setValue(s.server).onChange(async (v) => {
        s.server = v.trim() || DEFAULT_SERVER;
        await this.plugin.persist();
      }));

    new Setting(containerEl)
      .addButton((b) => b.setButtonText("Sync now").setCta().onClick(() => this.plugin.sync(true)));
  }
}
