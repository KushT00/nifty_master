import psycopg2
import boto3

auth_token = boto3.client('rds', region_name='ap-south-1').generate_db_auth_token(DBHostname='database-1-instance-1.crm4u0wa6yhn.ap-south-1.rds.amazonaws.com', Port=5432, DBUsername='postgres', Region='ap-south-1')

conn = None
try:
    conn = psycopg2.connect(
        host='database-1-instance-1.crm4u0wa6yhn.ap-south-1.rds.amazonaws.com',
        port=5432,
        database='postgres',
        user='postgres',
        password=auth_token,
        sslmode='require'
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute('SELECT version();')
    print(cur.fetchone()[0])
    cur.close()
except Exception as e:
    print(f"Database error: {e}")
    raise
finally:
    if conn:
        conn.close()