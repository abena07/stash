-- when a link was loved (👍), so the monthly roundup can place it in the right month (#44).
-- null for links loved before this existed.
ALTER TABLE links ADD COLUMN loved_at timestamptz;
