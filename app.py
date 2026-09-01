from config import DERIV_APP_ID, DERIV_PAT_TOKEN, DERIV_API_BASE
from api.deriv import DerivClient


def main():

    print("=" * 40)
    print(" DERIV STRATEGY LAB ")
    print("=" * 40)

    deriv = DerivClient(
        app_id=DERIV_APP_ID,
        access_token=DERIV_PAT_TOKEN,
        api_base=DERIV_API_BASE,
    )

    deriv.connect(account_type="demo")

    deriv.disconnect()


if __name__ == "__main__":
    main()
