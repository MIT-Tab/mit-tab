import os

from simple_rest_client.api import API

def construct_api():
    api = API(
        api_root_url='http://{tournmament_ip}/discord' % {
            'tournament_ip': os.environ.get('TOURNAMENT_IP', 'NONAME')
        },
        json_encode_body=True
    )

    api.add_resource(resource_name='debaters')
    api.add_resource(resource_name='judges')
    api.add_resource(resource_name='rooms')
    api.add_resource(resource_name='rounds')

    return api


def get_judge(discord_id):
    api = construct_api()

    response = api.judges.list(
        params={'discord_id': discord_id}
    )
    return response.body[0] if len(response.body) > 0 else None


def get_debater(discord_id):
    api = construct_api()

    response = api.debaters.list(
        params={'discord_id': discord_id}
    )
    return response.body[0] if len(response.body) > 0 else None


def is_judge(discord_id):
    api = construct_api()

    response = api.judges.list(params={'discord_id': discord_id})
    return len(response.body) > 0


def is_debater(discord_id):
    api = construct_api()

    response = api.debaters.list(params={'discord_id': discord_id})
    return len(response.body) > 0


def get_rooms():
    api = construct_api()

    response = api.rooms.list()
    print (response.body)
    return response.body


def get_rounds(round_number):
    api = construct_api()

    response = api.rounds.list(params={'round_number': round_number})
    return response.body
