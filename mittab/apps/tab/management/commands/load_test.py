from django.core.management.base import BaseCommand
import requests


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
            default="10")

    def handle(self, *args, **options):
        pass
