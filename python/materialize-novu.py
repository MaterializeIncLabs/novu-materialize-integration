#!/usr/bin/env python3

import hashlib
import logging
import os
import sys
import time

import psycopg2
from dotenv import load_dotenv
from novu.api import EventApi 

load_dotenv()

required_env_vars = [
    'NOVU_API_KEY',
    'NOVU_WORKFLOW_NAME',
    'MTZ_USER',
    'MTZ_PASSWORD',
    'MTZ_HOST',
    'MTZ_ALERT_VIEW',
    'MTZ_PERSIST_TABLE',
    'MTZ_ALERT_PAYLOAD'
]

for var in required_env_vars:
    if os.getenv(var) is None:
        sys.exit(f"Required environment variable {var} is not set.")

if os.getenv('RECIPIENTS_IN_PAYLOAD', None) is None and os.getenv('NOVU_RECIPIENTS', None) is None:
    sys.exit("Either RECIPIENTS_IN_PAYLOAD or NOVU_RECIPIENTS must be set.")

def setup_logging():
    log_level = os.getenv('LOG_LEVEL', 'WARNING').upper()
    log_output = os.getenv('LOG_OUTPUT', 'stdout')

    logger = logging.getLogger()
    logger.setLevel(log_level)

    if log_output == 'stdout':
        handler = logging.StreamHandler(sys.stdout)
    elif log_output == 'stderr':
        handler = logging.StreamHandler(sys.stderr)
    else:
        # Default to logging to a file if LOG_OUTPUT is set to a filename
        handler = logging.FileHandler(log_output)

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


def create_timestamp_persist_table(conn):
    with conn.cursor() as cur:
        logger.info('creating the persist table for timestamp tracking if it does not yet exist')
        cur.execute("CREATE TABLE IF NOT EXISTS {} (timestamp numeric);".format(os.getenv("MTZ_PERSIST_TABLE")))
    with conn.cursor() as cur:
        cur.execute("SELECT timestamp FROM {} LIMIT 1".format(os.getenv('MTZ_PERSIST_TABLE')))
        if cur.fetchone():
            logger.info('found existing value in timestamp persist table. do not need to initialize.')
            return
    with conn.cursor() as cur:
        logger.info('initializing new timestamp persist table with a zero value')
        cur.execute("INSERT INTO {} VALUES (0)".format(os.getenv('MTZ_PERSIST_TABLE')))


def store_timestamp_in_table (conn, timestamp):
    with conn.cursor() as cur:
        logger.info('storing timestamp in persist table')
        try:
            cur.execute("UPDATE {} SET timestamp = {}".format(os.getenv("MTZ_PERSIST_TABLE"), timestamp))
        except Exception as e:
            logger.error(f'unable to store timestamp in persist table: {e}')
            raise


def retrieve_timestamp_from_table(conn):
    with conn.cursor() as cur:
        logger.info('getting timestamp from persistence table')
        try:
            cur.execute("SELECT timestamp FROM {} LIMIT 1".format(os.getenv('MTZ_PERSIST_TABLE')))
        except Exception as e:
            logger.error(f"unable to retrieve timestamp from persistence table. will use current time. {e}")
            return None
        as_of = int(cur.fetchone()[0])
         # only return saved timestamp if it was in retain history
        if as_of > ( time.time() - int(os.getenv('RETAIN_HISTORY', 60)) * 60 )  * 1000:
            logger.info(f'found timestamp {as_of} in persist table. will start alerting from that time.')
            return as_of
        else:
            logger.info(f'persisted timestamp {as_of} was outside of retain history window of {os.getenv('RETAIN_HISTORY')} minutes, so will start from now.')
            return None


def process_payload(row):

    colnum = 3 # results start on col 3 when using progress
    payloadcontent = {}
    transaction_id_builder = []
    for col in os.getenv('MTZ_ALERT_PAYLOAD').split(','):
        try:
            col.rstrip('\n').rstrip(' ')
            payloadcontent[col] = row[colnum]
            transaction_id_builder.append(str(row[colnum]))
        except Exception as e:
            logger.error(f"unable to get result for declared payload column {col} in result column number {colnum}")
            raise
        colnum += 1
    transaction_id_joined = '|'.join(transaction_id_builder)
    transaction_id = hashlib.md5(transaction_id_joined.encode('utf-8')).hexdigest()
    return payloadcontent, transaction_id


def parse_recipients(payload):

    recipients = []
    recipients_in_payload = os.getenv('RECIPIENTS_IN_PAYLOAD', None)
    if recipients_in_payload:
        if recipients_in_payload not in payload:
            raise ValueError(f"Recipients column {recipients_in_payload} not found in payload")
        try:
            recipients.extend(payload[recipients_in_payload].split(','))
        except Exception as e:
            logger.warning(f"unable to parse recipients from payload column {recipients_in_payload}. {e}")
    try:
        recipients.extend(os.getenv('NOVU_RECIPIENTS', '').split(','))
    except Exception as e:
        logger.error(f"unable to parse recipients from environment variable NOVU_RECIPIENTS. {e}")
        raise e
    if not recipients:
        raise ValueError("No recipients found in payload or environment variable NOVU_RECIPIENTS")
    return recipients
    

logger = setup_logging()

logger.info('configuring Novu.')
novu_event_api = EventApi("https://api.novu.co", os.getenv('NOVU_API_KEY') )

dsn = "user={} password={} host={} dbname={} port=6875 sslmode=require options='--cluster={}'".format(
    os.getenv('MTZ_USER'), 
    os.getenv('MTZ_PASSWORD'),
    os.getenv('MTZ_HOST'),
    os.getenv('MTZ_DATABASE', 'materialize'),
    os.getenv('MTZ_CLUSTER', 'quickstart'),    
)
logger.info('making connection to Materialize for subscribe')
conn = psycopg2.connect(dsn)
logger.info('making connection to Materialize for progress tracking')
conn2 = psycopg2.connect(dsn)
conn2.set_session(autocommit=True)

create_timestamp_persist_table(conn2)

as_of = retrieve_timestamp_from_table(conn2) or int(round(time.time() * 1000))

with conn.cursor() as cur:
    logger.info('declaring cursor for subscribe')
    cur.execute(
        "DECLARE c CURSOR FOR SUBSCRIBE ( SELECT {} FROM {} ) with (snapshot false, progress true) AS OF {}; ".format(
            os.getenv('MTZ_ALERT_PAYLOAD'),
            os.getenv('MTZ_ALERT_VIEW'),
            as_of
        )
    )
    while True:
        logger.info('fetching from cursor')
        cur.execute("FETCH ALL c WITH (timeout='1s')")
        logger.info(f'found {cur.rowcount} rows')
        for row in cur:
            logger.info(f'row: {row}')
            last_timestamp = int(row[0])
            if str(row[1]).lower() == 'true':
                logger.info('this is a progress message row. continuing')
                continue
            if int(row[2]) == -1:
                if str(os.getenv('SEND_RETRACTIONS', False)).lower == 'false':
                    logger.info('this is a retraction row. skipping because SEND_RETRACTIONS is False.')
                    continue
                else:
                    logger.info('this is a retraction row. deleting alert.')
                    payloadcontent, transaction_id = process_payload(row)
                    if str(os.getenv('TEST_MODE', 'False')).lower() != 'true':
                        logger.info(f'calling Novu delete with transaction_id {transaction_id}')
                        novu_event_api.delete(
                            transaction_id=transaction_id,
                        )
                    else:
                        logger.info(f'in test mode so not sending delete to Novu. transaction_id: {transaction_id} payload: {payloadcontent}')
            payloadcontent, transaction_id = process_payload(row)
            recipients = parse_recipients(payloadcontent)
            if str(os.getenv('TEST_MODE', 'False')).lower() != 'true':
                logger.info(f'calling Novu with payload {payloadcontent}, transaction_id {transaction_id}')
                novu_event_api.trigger(
                    name=os.getenv('NOVU_WORKFLOW_NAME'),
                    recipients=recipients,
                    payload=payloadcontent,
                    transaction_id=transaction_id
                )
            else:
                logger.info(f'in test mode so not sending alert to Novu. transaction_id: {transaction_id} payload: {payloadcontent}')
            if last_timestamp > as_of:
                logger.info('timestamp has advanced since last row. storing timestamp in persist.')
                as_of = last_timestamp
                store_timestamp_in_table(conn2, as_of)
        if last_timestamp < ( time.time() - ( int(os.getenv("PROGRESS_TIMEOUT_MINUTES", 10)) * 60 * 1000 ) ):
            logger.error('we have not seen a progress update within the timeout threshold. kill the process so we can retry.')
            sys.exit(124)