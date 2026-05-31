"""Run Nexus license database migrations."""

from licensing import get_license_manager


def main():
    manager = get_license_manager()
    manager.migrate()
    print(f"License database ready: {manager.db_path}")


if __name__ == "__main__":
    main()
