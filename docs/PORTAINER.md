# Deploying on a server with Portainer

*[Nederlandse versie: PORTAINER.nl.md](PORTAINER.nl.md)*

These are the steps to run navimow2mqtt on your home server, as a stack in Portainer.

## Why not just `docker-compose.yml`?

That file builds the image from the project folder (`build: .`). That folder is not on your server, and Portainer cannot
build it there. So you build the image **once on your PC** and transfer it as a file. The stack then only uses
`docker-compose.portainer.yml`.

## Step 0: which processor does your server have?

Open a terminal on the server (SSH, or in Portainer: Containers, the Portainer container, Console) and run:

```bash
uname -m
```

- `x86_64`: an ordinary PC or NUC. Use the standard build (step 1a).
- `aarch64` or `arm64`: for example a Raspberry Pi 4 or 5. Use step 1b.
- `armv7l`: an old 32-bit Pi. Open an issue and we will see what is needed.

## Step 1: build and save the image (on your PC)

In the project folder, PowerShell:

**1a. The server is x86_64** (the image you have locally now is suitable):

```bash
docker build -t navimow2mqtt:latest .
docker save navimow2mqtt:latest -o navimow2mqtt.tar
```

**1b. The server is ARM64**:

```bash
docker buildx build --platform linux/arm64 -t navimow2mqtt:latest --load .
docker save navimow2mqtt:latest -o navimow2mqtt.tar
```

The file `navimow2mqtt.tar` is about 120 MB.

## Step 2: put the image in Portainer

1. Portainer, **Images**, **Import**.
2. Choose `navimow2mqtt.tar`, **Upload**.
3. Afterwards `navimow2mqtt:latest` is in the list of images.

(If you have your own registry you can push the image there and use the full name in the stack.)

## Step 3: create the stack

1. Portainer, **Stacks**, **Add stack**.
2. Name: `navimow2mqtt`.
3. Choose **Web editor** and paste the contents of [`docker-compose.portainer.yml`](../docker-compose.portainer.yml).
4. Under **Environment variables** add (button "Add environment variable", or "Advanced mode" and paste):

   | Name | Value | Required |
   |---|---|---|
   | `MQTT_HOST` | the IP of your MQTT broker, e.g. `192.168.1.2` | yes |
   | `MQTT_USERNAME`, `MQTT_PASSWORD` | if your broker asks for credentials | no |
   | `PUBLIC_URL` | the address of **this server** for the sign-in page, e.g. `http://192.168.1.5:8099` | recommended |
   | `TIMEZONE` | `Europe/Amsterdam` (already the default) | no |
   | `LANGUAGE` | `nl` for a Dutch sign-in page (default `en`) | no |

   Put the password here and not in the compose file. A variable only reaches the container if it is also listed under
   `environment:` in the compose file (the supplied file already does that for the ones above).
5. **Deploy the stack**.

## Step 4: sign in once

The token you have on your PC lives in a Docker volume on your PC and does not come along. So on the server you sign in once:

1. Look at Containers, `navimow2mqtt`, **Logs**: it says `login required`.
2. Open `http://<server>:8099` in a browser.
3. Click *Sign in to Navimow* and sign in. You are sent back to the page automatically and are signed in (the page then
   shows *Signed in*). If that does not work, expand the grey *Trouble signing in?* line at the bottom and paste the
   address from the address bar (see the README).
4. Within a few seconds the log says `found 1 mower(s)` and `cloud MQTT connected`.

After that the token stays in the volume `navimow-data`, also when you update the stack or recreate the container.

## Step 5: stop the local copy on your PC

**Never run two copies at the same time.** They use the same MQTT client id and the same Navimow sign-in: they knock each
other off the broker and can invalidate each other's token. So stop the version on your PC:

```bash
docker compose down
```

## What to check

In an MQTT client on your server (or MQTT Explorer):

- `navimow/bridge/status` is `online`, `navimow/bridge/auth` is `ok`
- `navimow/mower/state` shows the status and battery
- if `PUBLIC_URL` is set: `navimow/bridge/web_url` contains the address of the sign-in page

## Updating to a new version

1. Rebuild on your PC and `docker save` (step 1).
2. Portainer: **Images**, **Import**, upload the new file (the old `latest` then gets the new version).
3. Stack, **Update the stack** with "Re-pull image" off and **Deploy**, or go to the container and choose **Recreate**.

Token, statistics and logs are kept (volume `navimow-data`).

## Fetching log files for analysis

The bridge writes to `/data/logs` (see the README). You can fetch them like this:

- **Via SSH on the server:**
  ```bash
  docker cp navimow2mqtt:/data/logs ./navimow-logs
  ```
- **Via Portainer:** Containers, `navimow2mqtt`, Console (Connect), then for example `ls -l /data/logs`. Downloading files
  from the volume is possible in recent Portainer versions via **Volumes**, `navimow-data`, **Browse**.

## Problems

| Problem | Cause |
|---|---|
| `exec format error` in the log | The image was built for the wrong processor (step 0 and 1b) |
| The stack does not start: *image not found* | The import (step 2) failed, or the name differs from `navimow2mqtt:latest` |
| `connected to MQTT broker` is missing | `MQTT_HOST` is wrong or not reachable from the container. If the broker runs on the same server in another container, use the server's IP, not `localhost` |
| The sign-in page does not load | Port 8099 is already in use, or a firewall blocks it. Change the left side of the `ports` line, e.g. `"8100:8099"`, and match `PUBLIC_URL` |
| Signing in again is needed | See the README: the bridge shows `auth = required`, also on `navimow/bridge/auth` |

## If you do not want Home Assistant sensors

`MQTT_DISCOVERY` defaults to `false` in this file, because openHAB does not use it. If you had discovery on before, retained
configuration messages under `homeassistant/.../navimow2mqtt_...` are still on your broker. You can clear them with an MQTT
client (an empty retained message to the same topic).
