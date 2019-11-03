import re
from threading import Thread
import time

from django.core.management.base import BaseCommand
import requests

from mittab.apps.tab.models import Round, TabSettings
from mittab.apps.tab.management.commands import utils


class Command(BaseCommand):
    help = "Load test the tournament, connecting via localhost and hitting the server"

    def add_arguments(self, parser):
        parser.add_argument(
            "--host",
            dest="host",
            help="The hostname of the server to hit",
            nargs="?",
            default="localhost:8000")
        parser.add_argument(
            "--connections",
            dest="connections",
            help="The number of concurrent connections to open",
            nargs="?",
            default=10,
            type=int)

    def handle(self, *args, **options):
        cur_round = TabSettings.get("cur_round") - 1
        host = options["host"]

        csrf_threads = []
        rounds = Round.objects.filter(round_number=cur_round, victor=Round.NONE)
        for round_obj in rounds:
            judge = round_obj.chair
            csrf_threads.append(GetCsrfThread(host, judge.ballot_code, round_obj))

        num_errors = 0
        while csrf_threads:
            cur_csrf_threads = []
            for _ in range(min(len(csrf_threads), options["connections"])):
                cur_csrf_threads.append(csrf_threads.pop())

            for thr in cur_csrf_threads:
                thr.start()
            for thr in cur_csrf_threads:
                thr.join()

            result_threads = []
            for thr in cur_csrf_threads:
                num_errors += num_errors
                csrf_token, num_errors = thr.result
                if csrf_token is None:
                    print("no csrf token")

                result_thread = SubmitResultThread(
                    thr.host,
                    thr.ballot_code,
                    csrf_token,
                    thr.round_obj)
                result_threads.append(result_thread)

            for thr in result_threads:
                thr.start()
            for thr in result_threads:
                thr.join()
            for thr in result_threads:
                num_errors += thr.num_errors
            print("Done with one batch! Sleeping!")
            time.sleep(2)

        print("Done!")
        print("Total errors: %s" % num_errors)


class SubmitResultThread(Thread):
    MAX_ERRORS = 10

    def __init__(self, host, ballot_code, csrf_token, round_obj):
        super(SubmitResultThread, self).__init__()
        self.host = host
        self.ballot_code = ballot_code
        self.csrf_token = csrf_token
        self.round_obj = round_obj
        self.num_errors = 0
        self.resp = None

    def run(self):
        self.resp = self.get_resp()

    def get_resp(self):
        if self.num_errors >= self.MAX_ERRORS:
            return None

        result = utils.generate_random_results(self.round_obj, self.ballot_code)
        result["csrfmiddlewaretoken"] = self.csrf_token

        resp = requests.post("http://%s/e_ballots/%s/" % (self.host, self.ballot_code),
                             result,
                             cookies={"csrftoken": self.csrf_token})
        if resp.status_code > 299:
            self.num_errors += 1
            return self.get_resp()
        else:
            return resp.text


class GetCsrfThread(Thread):
    REGEX = "name=\"csrfmiddlewaretoken\" value=\"([^\"]+)\""
    MAX_ERRORS = 10

    def __init__(self, host, ballot_code, round_obj):
        super(GetCsrfThread, self).__init__()
        self.num_errors = 0
        self.host = host
        self.ballot_code = ballot_code
        self.round_obj = round_obj

    def run(self):
        resp = self.get_resp()
        if resp is None:
            self.result = (None, self.num_errors)
        else:
            csrf = re.search(self.REGEX, resp).group(1)
            self.result = (csrf, self.num_errors)

    def get_resp(self):
        if self.num_errors >= self.MAX_ERRORS:
            return None
        resp = requests.get("http://%s/e_ballots/%s"  % (self.host, self.ballot_code))
        if resp.status_code > 299:
            self.num_errors += 1
            return self.get_resp()
        else:
            return resp.text
