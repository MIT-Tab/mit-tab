from mittab.apps.tab.models import Debater, Team, Judge, Round, Room
from rest_framework import routers, serializers, viewsets


class DebaterSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Debater
        fields = ['name', 'discord_id']


class JudgeSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Judge
        fields = ['name', 'discord_id', 'ballot_code']


class RoomSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Room
        fields = ['name']


class TeamSerializer(serializers.HyperlinkedModelSerializer):
    debaters = DebaterSerializer(many=True, read_only=True)
    class Meta:
        model = Team
        fields = ['name', 'debaters']


class RoundSerializer(serializers.HyperlinkedModelSerializer):
    gov_team = TeamSerializer(many=False, read_only=True)
    opp_team = TeamSerializer(many=False, read_only=True)
    judges = JudgeSerializer(many=True, read_only=True)
    room = RoomSerializer(many=False, read_only=True)
    
    class Meta:
        model = Round
        fields = ['round_number',
                  'gov_team',
                  'opp_team',
                  'judges',
                  'room']


class DebaterViewSet(viewsets.ModelViewSet):
    queryset = Debater.objects.all()
    serializer_class = DebaterSerializer
    filterset_fields = ['name', 'discord_id']


class JudgeViewSet(viewsets.ModelViewSet):
    queryset = Judge.objects.all()
    serializer_class = JudgeSerializer
    filterset_fields = ['name', 'discord_id']


class RoomViewSet(viewsets.ModelViewSet):
    queryset = Room.objects.all()
    serializer_class = RoomSerializer
    filterset_fields = ['name']


class RoundViewSet(viewsets.ModelViewSet):
    queryset = Round.objects.all()
    serializer_class = RoundSerializer
    filterset_fields = ['round_number']


router = routers.DefaultRouter()
router.register(r'debaters', DebaterViewSet)
router.register(r'judges', JudgeViewSet)
router.register(r'rooms', RoomViewSet)
router.register(r'rounds', RoundViewSet)
