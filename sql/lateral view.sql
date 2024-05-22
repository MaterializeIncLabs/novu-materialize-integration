CREATE TABLE 
	materialize.auction.auction_alerts 
	(
		alert_name VARCHAR,  
		price_above INT, 
		item_name VARCHAR
	);

INSERT INTO 
	materialize.auction.auction_alerts 
VALUES 
	('expensive pizza', 90, 'Best Pizza in Town' ), 
	('all art', 0, 'Custom Art');

CREATE VIEW active_alerts AS
	SELECT 
		alert_name,
        id as auction_id,
		item_name,
        amount as price 
	FROM 
	(
		SELECT 
            id,
			item, 
			amount 
		FROM 
			materialize.auction.winning_bids
	) p,
	LATERAL (
		SELECT 
			price_above, 
			item_name, 
			alert_name 
		FROM 
			materialize.auction.auction_alerts a
		WHERE 
			a.item_name = p.item
		AND 
			a.price_above <= p.amount
);

CREATE INDEX 
	active_alerts_idx 
ON 
	active_alerts (alert_name,alert_name)
WITH 
	(RETAIN HISTORY FOR '1hr');



