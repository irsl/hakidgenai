# Hakidgenai (Home Assistant Kid Generative AI)

A simple dummy fun project to play some GenAi generated text to the kids
over DLNA on the TV screen in the living room. Triggerable/controllable
via Home Assistant.

## Setup
Example Ansible setup:

```
  - name: hakidgenai dirs
    ansible.builtin.file:
      path: "/data/hakidgenai/{{ item }}"
      owner: user
      group: user
      mode: '0775'
      state: directory
    with_items:
    - config
    - data

  - name: hakidgenai files
    ansible.builtin.copy:
      src: files/hakidgenai/
      dest: /data/hakidgenai/config

  - name: hakidgenai container
    docker_container:
      name: hakidgenai
      image: ghcr.io/irsl/hakidgenai:latest
      user: '1000:1000'
      env:
         GOOGLE_APPLICATION_CREDENTIALS=/etc/hakidgenai/sa.json
         PROMPTS_FILE=/etc/hakidgenai/prompts.json
         GOOGLE_API_KEY=AIza...
         DMS_URL_BASE=http://10.6.8.146:10001/
      volumes:
      - /data/hakidgenai/config:/etc/hakidgenai
      - /data/hakidgenai/data/:/var/lib/hakidgenai
      restart_policy: unless-stopped
      network_mode: host
```

Place your `sa.json` (Google Cloud service account key) and prompts file (example: `prompts.json`)
in the `files` subdir so it can be uploaded.

Then, the Home Assistant side of the setup is:

Your `configuration.yaml`:

```
rest_command:
  hakidgenai_request:
    url: "http://10.6.8.146:10001/pick"
```

Then your action could look like this:

```
- action: rest_command.hakidgenai
  response_variable: hakidgenai_response
- action: media_player.play_media
  target:
    entity_id: media_player.samsung_the_frame_50_2
  data:
    media_content_type: audio/mp3
    media_content_id: "{{ hakidgenai_response['content']['url'] }}"
```

You may trigger it using the favourite plush of your kids, like this one:

![Bing rabbit](https://github.com/irsl/hakidgenai/blob/main/bing.jpg?raw=true)
