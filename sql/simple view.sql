CREATE VIEW materialize.auction.expensive_pizza AS
SELECT 
    item,
    amount
FROM 
    materialize.auction.winning_bids
WHERE 
    item = 'Best Pizza in Town'
AND 
    amount > 90


CREATE INDEX 
    expensive_pizza_idx 
ON 
    expensive_pizza (item,amount)
WITH 
    (RETAIN HISTORY FOR '1hr');