# required:
name: Life and Ministry Meeting Workbook
strategy: polling # polling | webhook | static
refresh_interval: 1440 # minutes; the source only changes weekly, daily is plenty

# polling strategy only:
# Replace YOUR_USERNAME/YOUR_REPO below with your actual GitHub repo
# once you've pushed this project and enabled the Actions workflow.
polling_url: https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/data/mwb.json
polling_headers: ''
polling_verb: GET

# generic options:
no_screen_padding: 'no'
dark_mode: 'no'
