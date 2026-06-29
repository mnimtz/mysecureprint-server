# Pairing the MySecurePrint iOS app with this server

For end-users.

## Step 1 — get your server URL + token

1. Visit your server's URL (e.g. `https://your-server.azurewebsites.net`) in a browser
2. Sign in (local account or "Sign in with Microsoft" if the operator enabled Entra SSO)
3. Open `/my/connect` from the top-right menu
4. Two pieces of information you need:
   - **Server URL** — typically `https://your-server.azurewebsites.net`
   - **Bearer Token** — click the 👁 reveal button next to it, then 📋 copy

## Step 2 — set up the iOS app

1. Install **MySecurePrint** from the App Store
2. On first launch: tap **Setup**
3. Enter the **Server URL** from above
4. Tap **Sign in with Microsoft** (PKCE-based, opens a Safari sheet)
5. After Microsoft sign-in succeeds the app receives a token and stores it in the Keychain
6. Done — Cards / Management / Share Extension all work now

If your operator has **only local accounts** (no Entra SSO):
- Replace step 4 with: paste the Bearer Token from `/my/connect` into the "Manual token" field

## Step 3 — print from any iOS app

- Open any document in Files, Mail, Photos, etc.
- Tap the **Share** button → choose **MySecurePrint**
- Pick a target printer → confirm → done

The server converts Word/Excel/PowerPoint/JPG/PNG to PCL XL automatically before forwarding to Printix.
