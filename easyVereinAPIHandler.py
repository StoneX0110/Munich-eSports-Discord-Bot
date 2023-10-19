import json
from datetime import date, datetime, timedelta
import requests

with open('config.json', 'r') as f:
    config = json.load(f)

API_TOKEN = config['API_token']
header = {'Authorization': f'Token {API_TOKEN}'}


def get_member_joins_and_leaves():
    res = requests.get(
        'https://easyverein.com/api/v1.6/member?query={joinDate,resignationDate,customFields}&limit=1000',
        headers=header)
    members = _get_all_results(res)
    joined_member_ids = _get_latest(members, 'joinDate')
    left_member_ids = _get_latest(members, 'resignationDate')
    return joined_member_ids, left_member_ids


# TODO
def set_birthday_wishes_checkbox():
    res = requests.patch("https://easyverein.com/api/v1.6/member/1253810/custom-fields/178015180", headers=header)


def get_discord_id_if_allowed(member_url):
    res = requests.get(member_url + "/custom-fields?limit=1000", headers=header)
    custom_fields = _get_all_results(res)
    if _allows_birthday_wishes(custom_fields):
        return _find_discord_id(custom_fields)
    return 0


def get_birthday_members():
    members = _get_members()
    birthday_members = []
    for member in members:
        _convert_to_date(member, 'dateOfBirth')

        birthday = member['dateOfBirth']
        today = date.today()

        if birthday.month == today.month and birthday.day == today.day:
            birthday_members.append(member['member'])

    return birthday_members


def _get_discord_id(custom_fields_url):
    res = requests.get(custom_fields_url + "?limit=1000", headers=header)
    custom_fields = _get_all_results(res)
    return _find_discord_id(custom_fields)


def _get_latest(members, key):
    results = []
    for member in members:
        if member[key] is not None:
            _convert_to_date(member, key)
            if date.today() - timedelta(days=1) == member[key]:  # member joined/left yesterday
                results.append(_get_discord_id(member['customFields']))
    return results


def _allows_birthday_wishes(custom_fields):
    for field in custom_fields:
        if '177910549' in str(field['customField']):
            return field['value'] == 'True'


def _find_discord_id(custom_fields):
    for field in custom_fields:
        if '34867055' in str(field['customField']):
            return field['value']


def _get_members():
    res = requests.get('https://easyverein.com/api/v1.6/contact-details?query={dateOfBirth, member}&limit=1000',
                       headers=header)
    all_results = _get_all_results(res)
    return [member for member in all_results if member['member'] is not None and member['dateOfBirth'] is not None]


def _get_all_results(res):
    all_results = res.json()['results']
    next_page = res.json()['next']
    while next_page:
        res = requests.get(next_page, headers=header)
        all_results.extend(res.json()['results'])
        next_page = res.json()['next']
    return all_results


def _convert_to_date(member, field):
    member[field] = datetime.fromisoformat(str(member[field])).date()
