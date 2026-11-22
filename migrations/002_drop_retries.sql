-- retries and alerts for failed drops (#16)

-- where a server owner wants failure alerts; null = dm the owner
ALTER TABLE guilds ADD COLUMN alert_channel_id bigint;

-- a scheduled drop is due while next_attempt_at <= now and it is neither sent nor failed.
-- drops sent from commands never set next_attempt_at, so the scheduler ignores them.
ALTER TABLE drops
    ADD COLUMN attempts        smallint NOT NULL DEFAULT 0,
    ADD COLUMN last_error      text,
    ADD COLUMN next_attempt_at timestamptz,
    ADD COLUMN failed_at       timestamptz;

CREATE INDEX drops_due_idx ON drops (next_attempt_at)
    WHERE sent_at IS NULL AND failed_at IS NULL;
