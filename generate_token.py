import os
from google_auth_oauthlib.flow import InstalledAppFlow

# Fixes the (insecure_transport) OAuth 2 MUST utilize https error for localhost testing
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

SCOPES = ['https://www.googleapis.com/auth/drive']

def main():
    cred_file = "credentials.json"
    if not os.path.exists(cred_file):
        print(f"Error: {cred_file} not found. Please upload it first.")
        return

    # Initialize the flow
    flow = InstalledAppFlow.from_client_secrets_file(cred_file, SCOPES)
    
    # We must match one of the redirect URIs exactly. The credentials.json has "http://localhost".
    # Sometimes it needs to be an exact port, but if it has "http://localhost", we can use that.
    flow.redirect_uri = 'http://localhost'

    auth_url, _ = flow.authorization_url(prompt='consent')

    print("\n" + "=" * 60)
    print("1. Click this link and sign in with your Google Account:")
    print("\n" + auth_url + "\n")
    print("2. After you grant permissions, your browser will redirect you to a URL")
    print("   that starts with http://localhost/?state=... and might say 'Site can't be reached'.")
    print("3. DO NOT PANIC! Just copy the ENTIRE URL from your browser's address bar.")
    print("=" * 60 + "\n")

    auth_response = input("Paste the entire URL here: ").strip()

    if not auth_response:
        print("No URL provided.")
        return
        
    try:
        # fetch the token using the response URL
        flow.fetch_token(authorization_response=auth_response)
        creds = flow.credentials
        
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
            
        print("\nSuccess! token.json has been created.")
        print("Move it using: mv token.json config/")
    except Exception as e:
        print(f"\nError verifying your URL: {e}")

if __name__ == '__main__':
    main()
