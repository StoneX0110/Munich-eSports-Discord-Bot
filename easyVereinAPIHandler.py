import json
from datetime import date
import requests

with open('config.json', 'r') as f:
    config = json.load(f)

API_TOKEN = config['API_token']
header = {'Authorization': f'Token {API_TOKEN}'}

# TODO implement automatic conversion of discord tags to discord ids


def get_discord_id(member_url):
    member = requests.get(member_url+"?query={customFields}", headers=header).json()
    res = requests.get(member['customFields'], headers=header)
    custom_fields = res.json()['results']
    for field in custom_fields:
        # TODO need to check for birthday-post-consent checkbox
        if '34867055' in str(field['customField']):
            return field['value']


def get_birthday_members():
    members = _get_members()
    birthday_members = []
    for member in members:
        _convert_birthday_to_date(member)

        birthday = member['dateOfBirth']
        today = date.today()

        if birthday.month == today.month and birthday.day == today.day:
            birthday_members.append(member)

    return birthday_members


def _get_members():
    res = requests.get('https://easyverein.com/api/v1.6/contact-details?query={dateOfBirth, member}&limit=1000',
                       headers=header)
    all_results = _get_all_results(header, res)
    return [member for member in all_results if member['member'] is not None and member['dateOfBirth'] is not None]


def _get_all_results(header, res):
    all_results = res.json()['results']
    next_page = res.json()['next']
    while next_page:
        res = requests.get(next_page, headers=header)
        all_results += res.json()['results']
        next_page = res.json()['next']
    return all_results


def _convert_birthday_to_date(member):
    member['dateOfBirth'] = date.fromisoformat(str(member['dateOfBirth']))
