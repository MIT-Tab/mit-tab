Running a Tournament
====================

This document will cover anything you need to do after all the teams, judges,
and rooms are registered and checked in.

For each round after round 1, you need to enter results, make sure you have
enough judges, and pair the next round. **Having multiple people logged in and
entering result information and checking that information makes your tournament
run significantly better (faster and more accurate).** It is **highly**
advised that you do this.

Entering Results
----------------

After pairing a round, you will mostly be dealing with the current round's
pairing view located at `/pairings/status/`. This is kind of the control center
for the whole tournament, and you can change pretty much anything from this
view.  The areas of importance are shown below:
![](img/result_entry_colored.png)

* Red: This area shows you basic information about the government and
  opposition teams.  Use this to quickly check if the pairings make sense.
* Green: This area shows the judge(s) assigned to a round. In the case of a
  panel, the chair is the one with the highest rank, not necessarily the one at
  the top. Click the "NA" button to select panelists
* Yellow: This is where you click to enter results for that round. Note that
  whenever entering forfeit wins or losses, enter speaks **of zero** and assign
  ranks arbitrarily.  The program will do the right thing. Possible results are
  1. Gov/Opp win: The government or opposition team won outright, this is 99%
     of results.
  2. Gov/Opp win via forfeit: The government or opposition team won via a
     forfeit, e.g. their opponents did not show up. The winning team will get
     average speaks and ranks for the round and the losing team will get
     speaks of zero and ranks of 7.  You can manually assign the losing team
     speaks greater than zero if you don't want to totally tank their speaks,
     but they will continue to get ranks of 7 (this is potentially changing
     going forwards).
  3. All Drop: Both teams lose the round. Everyone gets speaks of zero and
     ranks of 7.  Use this for when both teams don't show up.
  4. All Win: Both teams win the round.  Useful for when a judge does not show
     up to a round and you don't want the tournament to run behind. Both teams
     get average speaks and ranks for that round (recalculated with every
     round).
* Blue: This is where you click to edit the round in the administration
  interface.  From that interface you can change anything you want about the
  round, e.g. which teams are debating, which judge is judging, which room the
  debate is in, etc ...  Note that you can also drag and drop judges around as
  well as teams from the pairing view, but only within the pairing (i.e. you
  can't drag a judge that wasn't paired in into the pairing, you can make this
  change from the admin interface, however).  Please do not delete rounds
  unless you also delete the corresponding Round Stats (viewable from the admin
  interface) for the debaters in that round.  You really should not need to
  ever delete a round, the various options for the results of a round should be
  sufficient.

Pairing the Next Round
----------------------

To pair a round, navigate to `/pairings/status/` and hit "Prepare Next Round".
For any round after round 1, make sure that all results have been entered.
After that, you should see this page:

![](img/pairing_checks.png)

This signals that it is safe to pair the round. Backups before and after the
pairing will automatically be created for you and labeled with the round number
in case you need to restore from backups due to a pairing error.

Afterwards, hit the "Assign Judges" button to pair judges into the rounds.

Backing Up
----------

MIT-TAB supports the concept of "backups" which allow you to create full
backups of the state of your tournament at any given moment. Treat your
tournament like a final paper: save early, and save often.

You will automatically get backups before and after each pairing event, labeled
with the round number.  If you manually backup a timestamp will be appended to
the name so that you can tell which is which. Eventually, we plan to support
arbitrary naming, but that is not ready yet.

Use cases:

1. You pair a round but need to re-pair because teams showed up that were
   checked out, or a bunch of judges shows up late, etc ...

2. If you download the backups it can also serve as a crash prevention system.
   If for whatever reason your server goes down, you can start up your
   tournament on another computer using the downloaded backup file.

### Creating a back-up

1. Under the "Backups" menu in the navigation bar, select "Backup Current"
2. You will now be redirected to the lists page, where you can see the backup
   `manual_backup_round_{x}_{timestamp}` file
3. (Optional) Click on the backup file and click the "Download Backup" copy to
   have a local version just in case


### Restoring from a back-up

1. (Recommended) Create a back-up of your current tournament state using the
   instructions above, in case you need to access it again.
2. Under the "Backups" menu, select "View Backups"
3. Find the back-up you're looking for. Auto-generate back-ups are named
   clearly. Manually backups have a imestamp at the end of them
4. Click on the back-up and click "Restore From Backup"

NOTE: You may be logged out after restoring from a back-up. The
username/password is still the same.

Re-pairing a round
------------------

If something went wrong in the pairings, you may want to pair the round again.
In order to do this, all you have to do is restore from the before pairing
back-up and then pair the round as described [above](#pairing-the-next-round)

The find the back-up to restore from, go to "Backups" > "View Backups" and
click on the one with the name `round_x_before_pairing.db`, where `x` is the
round number that you want to re-pair.

Removing Teams, Rooms and Judges
--------------------------------

Throughout a tournament, you may have to remove a room, drop a team, etc.
There used to be a delete button, but deleting teams/rooms/judges can
potentially delete results from rounds that occurred, so that button was
removed.

If you want to delete an entry (rather than just checking it out):

1. Reconsider your decision. Why does checking it out not work?
2. Make sure that the judge/team/room/debater was not paired in to any rounds.
3. Delete it using the Admin Interface

**NOTE:** You will never have to remove a debater if there is still another
debater on the team. Just enter the results as an iron-person round.

### Removing a team

To remove a team, simply uncheck the "Checked in" checkbox at the bottom of
the team's detail page. Simply re-check this to add them back in

![](img/removing_a_team.png)

### Removing a judge

To remove a judge, uncheck the "Checked in for Round _" checkbox for each
round that you want to check them out for.

![Removing a judge](https://i.imgur.com/40wWLU4.png)

### Removing a room

To remove a room, un check it in for the given round. You must have enough checked in rooms for this to work.

![](img/removing_a_room.png)

Discord Integration
-------------------
MIT-Tab now supports a complete Discord integration. This integration syncs users on Discord with debaters and judges on MIT-Tab. It requires including Discord ID in file imports (see the import documentation). On tournament create, you will be asked to provide a valid Discord ID for your superuser -- this should be a member of your tournament staff. The starting email will also provide an invite to your discord server -- feel free to distribute this to participants, or generate a new one once you have accessed the Discord.

### Roles
The created roles are as follows:
- Staff
- EOs
- Judge
- Debater

These roles should be self-explanatory. EOs and staff have the ability to speak / write in protected channels whereas judges and debaters do not.

### Start of a Tournament
The check-in procedure is unchanged on MIT-Tab. We suggest you use the #checkins channel for the purpose. If a debater / judge is not linked properly to the Discord role (their name hasn't changed, and/or their color is listed incorrectly), please update their Discord ID to be correct on MIT-Tab, and then @mention them on Discord.  This will fix the problem.

### Discord Commands
There are a number of Discord commands that are supported:

- !rooms
  This will create the debate rooms (and clear existing rooms). This should be run before each round. Please run this AFTER all RFDs are concluded as it will kick debaters and judges out of their rooms.
- !send <user> |<room name>
  This will send the <user> to the <room name>, this is useful if a user's Discord ID is incorrect and you wish for them to be a debate room.
- !round <round number> blast
  This will blast the round's pairings to the #announcements channel on Discord
- !round <round number> send
  This will send the debaters and judges to their rooms. This takes maybe 3-5 seconds per room, so can become time consuming for large tournaments -- probably less than what it takes them to walk to a room on a physical campus.
- !spectate <room name>
  This can be used by debaters, judges, and users with no role, and will allow them to spectate a round.
- !invite
  This generates a public invite and will send the link in the channel the command was issued in.
- !code
  This will DM the sender their ballot code (if they are a judge)

