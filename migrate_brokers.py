"""Create broker profile database tables and seed the default MT5 profile."""

from brokers import get_broker_manager


def main():
    manager = get_broker_manager()
    profiles = manager.list_profiles()
    print(f"Broker profile database ready: {manager.db_path}")
    print(f"Profiles available: {len(profiles)}")


if __name__ == "__main__":
    main()
