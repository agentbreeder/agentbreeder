-- Reset the fixtures that golden cases mutate (cancellation requests on the seeded eval
-- customers). Runs before every eval run, against the agent database.
DELETE FROM cancellation_requests WHERE customer_id LIKE 'eval-cust-%';
UPDATE orders SET status = 'paid' WHERE id IN ('NW-90002', 'NW-90004');
