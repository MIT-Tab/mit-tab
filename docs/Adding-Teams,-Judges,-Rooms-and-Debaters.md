Adding Teams, Judges, Rooms, and Debaters
=========================================

There are two ways to enter teams, judges, rooms, and debaters:
[Manually entering individually](#manual-data-entry) or
[importing data in batches](#batch-data-import). In most cases, the batch
import is probably easier. However, you may need to enter data manually to add
one or two teams at a time, or to fix any errors that may have occurred when
importing data in bulk.

Batch Data Import
-----------------

To import data in bulk: First, navigate to the "File Data Upload" link under
the "Admin" section of the navigation menu:

Then, you should see a page where you can add a file for judges, rooms and teams:

![](img/data_import.png)

You can upload xlsx files through this page to import data in bulk. When there is an
error in the format of the xlsx files, all changes generated from the file are aborted,
even if some were valid, so that you can simply re-upload a corrected version of the
file

You can use [these templates](https://drive.google.com/drive/folders/1w5dWkOn_p6HD24pI04xR7lzDakQMmQk3)
for the teams, judges, and rooms files

**NOTE:** The files _must_ be `.xlsx` files. Not `.xls`, `.csv`, or anything
similar

**NOTE:** All data in the first row will be ignored. That row is assumed to be
a header row.

Here is the format of the .xlsx file:

\* = required field

### Teams (and Debaters)

**NOTE:** Debaters will automatically be created from this sheet, too.

```eval_rst
+-------+--------------------------+-----------------------+-------------------+-----------------+-------------------------+-------------------------------+-------------------------+--+
| Name* | School*                  | Hybrid School         | Seed              | Debater 1 Name* | Debater 1 Novice Status | Debater 2 Name*               | Debater 2 Novice Status |  |
|       | (If hybrid, school whose | (If hybrid, school    | (“Half”, “Full”,  |                 | (“N” or blank)          |                               | (“N” or blank)          |  |
|       | protection they take)    | whose protection they | “Free”, or empty) |                 |                         |                               |                         |  |
|       |                          | don't take, otherwise |                   |                 |                         |                               |                         |  |
|       |                          | leave empty)          |                   |                 |                         |                               |                         |  |
+-------+--------------------------+-----------------------+-------------------+-----------------+-------------------------+-------------------------------+-------------------------+--+
|       |                          |                       |                   |                 |                         |                               |                         |  |
+-------+--------------------------+-----------------------+-------------------+-----------------+-------------------------+-------------------------------+-------------------------+--+
```

### Judges

```eval_rst
+-------+-------+-----------------------------------+
| Name* | Rank* | Affiliated Schools (1 per column) |
+-------+-------+-----------------------------------+
|       |       |                                   |
+-------+-------+-----------------------------------+
```

### Rooms

```eval_rst
+-------+------------------------+
| Name* | Rank* (Decimal, 0-100) |
+-------+------------------------+
|       |                        |
+-------+------------------------+
```

### Scratches

```eval_rst
+------------+-------------+---------------------------------+--+
| Team Name* | Judge Name* | Scratch Type ("tab" or "team")* |  |
+------------+-------------+---------------------------------+--+
|            |             |                                 |  |
+------------+-------------+---------------------------------+--+
```

Manual Data Entry
-----------------

From the home page, you can add and view all _Schools, Judges, Teams, Debaters,
and Rooms_.  To enter information quickly, you should have multiple people
reading through your registration information and entering it into the program
at any given moment.

![](img/list_add_bar.png)
### Schools
1. Name - Name of the school, e.g. "Yale"

### Judges
1. Name - Name of the judge.
2. Rank - A number from 0.0 - 99.99 that represents the relative ranking of this
judge. Higher is a "better" judge.
3. Affiliated Schools - A list of schools that this judge should be unable to
judge. **Use this for team scratches as well as multiple affiliations**

### Teams
1. Name - Name of the team, e.g. "Yale A"
2. School - School that this team should be protected from in pairing. If you
are entering a hybrid, select the team that has protection.
3. Hybrid school - For hybrids, the school they are not taking protection from.
This will prevent the team from being judged by this school, but not from
hitting teams from that school
4. Debaters - The debaters on this team, up to two debaters may be chosen and
you can add a debater directly (instead of having to enter them separately)
using the button to the right of the selection box.  If you select one debater
then the program will treat the team as an iron man team.
5. Seed - The seed of the team, used during the first round pairing.
6. Checked in - If this box is checked then any rounds you pair will include
this team in the pairing. Uncheck this if you want the team to not be paired
into the rounds.
7. Scratch Count - Used for generating a form that allows you to immediately
add scratches.  Feel free to put zero and add scratches later (they can be
added from either the judge page or the team page at any time).

### Debaters
1. Name - Name of the debater, e.g. "Matt Smith".
2. Novice status - Varsity or novice, used by the program to determine novice
team/debater rankings.

### Rooms
1. Name - Name of the room.
2. Rank -  A number from 0.0 - 99.99 that represents the relative ranking of
this room.  Higher is a "better" room, and higher ranked rooms will be paired
in before other rooms.  **You can use ranks to "check out" a room by changing
rooms that you don't want to use to 0**
