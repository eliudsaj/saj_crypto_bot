"""Create SaaS user database and seed the bootstrap admin."""

from users.store import get_user_store


def main():
    store = get_user_store()
    print(f"User database ready: {store.db_path}")
    print(f"Users available: {len(store.list_users())}")


if __name__ == "__main__":
    main()
