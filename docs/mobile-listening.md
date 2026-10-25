# Listen and control from your phone

The Mac runs the station and must stay awake and connected to the internet. The phone uses the same UI, timeline, editor and settings as the desktop, through a private Tailscale network, including over mobile data. Station startup and shutdown stay on the local Mac UI.

1. Install and connect Tailscale on both devices using the same account.
2. Run `tailscale status --json` and copy the Mac's `Self.DNSName`, without its final dot. In the existing `[output]` section of `~/Library/Application Support/PersonalRadioClaude/config.toml`, set:

   ```toml
   listen_port = 8766
   control_host = "your-mac.your-tailnet.ts.net"
   ```

   Replace the example hostname with the exact Mac hostname. Use output mode `browser` or `both` so the station produces a stream.
3. Restart the radio **UI**. The phone UI binds to `127.0.0.1:8766`; the local desktop UI stays at `127.0.0.1:8765`. Both share one station, stream and per-run API token.
4. Run `tailscale serve --bg --https=443 http://127.0.0.1:8766`. If prompted, enable HTTPS through the Tailscale link using the owner's login. Leave the optional Funnel checkbox off. If port 443 already hosts another service, choose a free HTTPS port rather than replacing it; include that port in `control_host` and the phone URL.
5. In the local Mac UI, press **Start radio**, then open the HTTPS address on the phone. **Listen** and **Pause listening** affect only this device and never start the station. Next, editing and settings affect the shared station and every listener. **Start radio** and **Stop radio** are available only in the local Mac UI; the phone API rejects those commands too.

Only the exact configured remote Host is accepted on the phone port. API reads and commands still require the per-run token supplied by the page; another website cannot read that token or issue authorized commands. Reload open desktop and phone tabs after restarting the UI so they receive its new token. The token page is not cached.

Tailnet access controls determine who can control the station and view its timeline, settings and enabled integrations. Grant access only to trusted devices. Keep this deployment private: do not use Tailscale Funnel or publicly expose either HTTP port. The background Serve rule persists across Tailscale restarts; the radio UI must also be running. Account sign-ins whose callbacks use `127.0.0.1` still need to be completed in a browser on the Mac.

To disable phone access, run `tailscale serve --https=443 off`, set `listen_port = 0`, and restart the UI. Sleep, closing the lid, disconnecting Tailscale or stopping the station interrupts listening. Phone lock-screen/background playback and switching networks must be checked on the actual phone.
