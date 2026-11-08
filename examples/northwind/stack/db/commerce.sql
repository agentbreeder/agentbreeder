-- Commerce data for the Northwind support agent: customers, product catalog, orders.
-- Applied by the Compose `db-init` service on every `up`; idempotent (seed rows use
-- ON CONFLICT DO NOTHING, so edits you make in the database are kept).
-- Money is stored in integer cents (USD). Prices match the help-center articles in agent/kb/.

CREATE TABLE IF NOT EXISTS customers (
  id TEXT PRIMARY KEY,                 -- the customer_id the chat sends
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  plan TEXT NOT NULL CHECK (plan IN ('free', 'pro_monthly', 'pro_annual', 'team')),
  seats INTEGER NOT NULL DEFAULT 1 CHECK (seats > 0),
  country TEXT NOT NULL,
  created TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS products (
  sku TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT NOT NULL CHECK (category IN ('hardware', 'accessory', 'shipping', 'subscription')),
  description TEXT NOT NULL,
  price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
  unit TEXT NOT NULL DEFAULT 'each',   -- 'each', 'per user per month', 'per user per year'
  active BOOLEAN NOT NULL DEFAULT true);

CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES customers(id),
  status TEXT NOT NULL CHECK (status IN
    ('pending', 'paid', 'shipped', 'delivered', 'cancelled', 'returned', 'refunded')),
  ship_to_country TEXT,
  carrier TEXT,
  tracking_number TEXT,
  created TIMESTAMPTZ NOT NULL DEFAULT now(),
  shipped_at TIMESTAMPTZ,
  delivered_at TIMESTAMPTZ,
  total_cents INTEGER NOT NULL CHECK (total_cents >= 0));
CREATE INDEX IF NOT EXISTS orders_customer ON orders (customer_id, created DESC);

CREATE TABLE IF NOT EXISTS order_items (
  order_id TEXT NOT NULL REFERENCES orders(id),
  sku TEXT NOT NULL REFERENCES products(sku),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
  PRIMARY KEY (order_id, sku));

-- Cancellation requests filed by the agent; only a human approval (ops dashboard) cancels.
CREATE TABLE IF NOT EXISTS cancellation_requests (
  id TEXT PRIMARY KEY,                       -- CR-XXXXXXXX
  order_id TEXT NOT NULL REFERENCES orders(id),
  customer_id TEXT NOT NULL REFERENCES customers(id),
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
  requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  session TEXT, turn_id TEXT,                -- the conversation that filed it
  decided_at TIMESTAMPTZ, decided_by TEXT, decision_note TEXT);
-- at most one open request per order
CREATE UNIQUE INDEX IF NOT EXISTS cancellation_one_pending
  ON cancellation_requests (order_id) WHERE status = 'pending';

-- ---------------------------------------------------------------- seed: catalog
INSERT INTO products (sku, name, category, description, price_cents, unit) VALUES
  ('PEN-001', 'Northwind Pen', 'hardware',
   'Smart stylus that captures handwriting into notes. One-year warranty.', 7900, 'each'),
  ('PEN-TIPS-3', 'Replacement pen tips (3-pack)', 'accessory',
   'Three soft replacement tips for the Northwind Pen.', 1200, 'each'),
  ('PEN-SLEEVE', 'Felt pen sleeve', 'accessory',
   'Protective felt sleeve that fits the Northwind Pen.', 1900, 'each'),
  ('PEN-DOCK', 'USB-C charging dock', 'accessory',
   'Magnetic USB-C dock for charging the Northwind Pen.', 2900, 'each'),
  ('SHIP-EXPRESS-US', 'Express shipping (US only, 2 business days)', 'shipping',
   'Optional express delivery for US addresses. Standard shipping is free.', 1500, 'each'),
  ('PLAN-PRO-MONTHLY', 'Pro plan, billed monthly', 'subscription',
   'Unlimited notebooks, 100 GB storage.', 1000, 'per user per month'),
  ('PLAN-PRO-ANNUAL', 'Pro plan, billed annually', 'subscription',
   'Unlimited notebooks, 100 GB storage ($8 per user per month).', 9600, 'per user per year'),
  ('PLAN-TEAM', 'Team plan', 'subscription',
   'Pro features plus shared workspaces, admin console and SSO.', 1500, 'per user per month')
ON CONFLICT (sku) DO NOTHING;

-- ---------------------------------------------------------------- seed: customers
INSERT INTO customers (id, name, email, plan, seats, country) VALUES
  ('cust-001', 'Ava Patel', 'ava.patel@example.com', 'pro_annual', 1, 'US'),
  ('cust-002', 'Brightline Studio', 'billing@brightline.example.com', 'team', 25, 'US'),
  ('cust-003', 'Liam Chen', 'liam.chen@example.com', 'free', 1, 'CA'),
  ('cust-010', 'Sofia Rossi', 'sofia.rossi@example.com', 'pro_monthly', 1, 'GB'),
  ('cust-020', 'Noah Müller', 'noah.mueller@example.com', 'pro_monthly', 1, 'DE'),
  -- fixed customers used by the offline eval set (ops/golden.jsonl)
  ('eval-cust-a', 'Eval Customer A', 'eval-a@example.com', 'pro_annual', 1, 'US'),
  ('eval-cust-b', 'Eval Customer B', 'eval-b@example.com', 'team', 5, 'GB')
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------- seed: orders
INSERT INTO orders (id, customer_id, status, ship_to_country, carrier, tracking_number,
                    created, shipped_at, delivered_at, total_cents) VALUES
  ('NW-10021', 'cust-001', 'shipped', 'US', 'UPS', '1Z999AA10123456784',
   now() - interval '4 days', now() - interval '2 days', NULL, 9100),
  ('NW-10022', 'cust-001', 'delivered', 'US', 'USPS', '9400111899223197428490',
   now() - interval '40 days', now() - interval '38 days', now() - interval '35 days', 1900),
  ('NW-10030', 'cust-003', 'paid', 'CA', NULL, NULL,
   now() - interval '1 day', NULL, NULL, 7900),
  ('NW-10031', 'cust-002', 'delivered', 'US', 'FedEx', '771234567890',
   now() - interval '20 days', now() - interval '19 days', now() - interval '16 days', 39500),
  ('NW-10040', 'cust-010', 'cancelled', 'GB', NULL, NULL,
   now() - interval '9 days', NULL, NULL, 7900),
  ('NW-10041', 'cust-020', 'refunded', 'DE', 'DHL', 'JJD000390007788991',
   now() - interval '60 days', now() - interval '58 days', now() - interval '55 days', 7900),
  ('NW-90001', 'eval-cust-a', 'shipped', 'US', 'UPS', '1Z999AA10999000111',
   now() - interval '3 days', now() - interval '1 day', NULL, 10600),
  ('NW-90002', 'eval-cust-a', 'paid', 'US', NULL, NULL,
   now() - interval '6 hours', NULL, NULL, 1200),
  ('NW-90003', 'eval-cust-b', 'delivered', 'GB', 'Royal Mail', 'RM123456785GB',
   now() - interval '25 days', now() - interval '24 days', now() - interval '20 days', 15800),
  ('NW-90004', 'eval-cust-a', 'paid', 'US', NULL, NULL,
   now() - interval '2 hours', NULL, NULL, 2900)
ON CONFLICT (id) DO NOTHING;

INSERT INTO order_items (order_id, sku, quantity, unit_price_cents) VALUES
  ('NW-10021', 'PEN-001', 1, 7900), ('NW-10021', 'PEN-TIPS-3', 1, 1200),
  ('NW-10022', 'PEN-SLEEVE', 1, 1900),
  ('NW-10030', 'PEN-001', 1, 7900),
  ('NW-10031', 'PEN-001', 5, 7900),
  ('NW-10040', 'PEN-001', 1, 7900),
  ('NW-10041', 'PEN-001', 1, 7900),
  ('NW-90001', 'PEN-001', 1, 7900), ('NW-90001', 'PEN-TIPS-3', 1, 1200),
  ('NW-90001', 'SHIP-EXPRESS-US', 1, 1500),
  ('NW-90002', 'PEN-TIPS-3', 1, 1200),
  ('NW-90003', 'PEN-001', 2, 7900),
  ('NW-90004', 'PEN-DOCK', 1, 2900)
ON CONFLICT (order_id, sku) DO NOTHING;
