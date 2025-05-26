#!/usr/bin/env python3
"""
Script to refresh expired OAuth token
"""

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import json

def refresh_token():
    try:
        # Load the expired token
        creds = Credentials.from_authorized_user_file('combined_token.json')
        print(f'Token loaded, expired: {creds.expired}')

        # Refresh the token
        if creds.expired and creds.refresh_token:
            print('Refreshing token...')
            creds.refresh(Request())
            print('✅ Token refreshed successfully!')
            
            # Save the refreshed token
            with open('combined_token.json', 'w') as f:
                f.write(creds.to_json())
            print('✅ Refreshed token saved to combined_token.json')
            
            # Show new expiry
            token_data = json.loads(creds.to_json())
            print(f'New expiry: {token_data.get("expiry", "Not found")}')
            
        else:
            print('❌ Cannot refresh token - no refresh token available')
            
    except Exception as e:
        print(f'❌ Error refreshing token: {e}')

if __name__ == "__main__":
    refresh_token() 