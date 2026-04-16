import redis

def clean_redis_cache(pattern: str = 'report_cache:*', host: str = 'localhost', port: int = 6379, db: int = 0) -> int:
    """
    Clean Redis cache keys matching the given pattern.
    
    Args:
        pattern: Key pattern to match (default: 'report_cache:*')
        host: Redis host (default: 'localhost')
        port: Redis port (default: 6379)
        db: Redis database number (default: 0)
    
    Returns:
        Number of keys deleted
    """
    try:
        r = redis.Redis(host=host, port=port, db=db, decode_responses=True)
        r.ping()  # Verify connection
        
        keys = r.keys(pattern)
        deleted = r.delete(*keys) if keys else 0
        print(f'Deleted {deleted} keys matching pattern: {pattern}')
        return deleted
    except redis.ConnectionError as e:
        print(f'Error: Cannot connect to Redis - {e}')
        return 0
    except Exception as e:
        print(f'Error: {e}')
        return 0

if __name__ == '__main__':
    clean_redis_cache()