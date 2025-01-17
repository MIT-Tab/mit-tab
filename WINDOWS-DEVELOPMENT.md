# Guide to Setting Up the Development Environment

This is a guide to getting a minimum viable **software development** setup of MIT-Tab on Windows. If you're a tournament or tab director looking for help on how to use mit-tab, please reference [the docs](https://mit-tab.readthedocs.io/en/latest/) instead.


## Preliminary notes and Disclaimers
1. This guide is meant to get new devs off the ground as quickly as possible, so it is deliberately opinionated, and there are plenty of other ways to reach similar outcomes.
2. This guide has also only been tested on Windows. Ubuntu or other Debian-based Linux users should be able to follow along, skipping step 1, but for MacOS and non-Debian Linux distributions, most of these specific commands won't work, and this should just serve as a rough guideline on what needs to be installed, and in roughly what order.
3. This guide assumes surface-level familiarity with software development, using IDEs, and bash commands. Specific familiarity with these libraries, technologies, and package managers is not strictly required, but may become needed if unexpected issues surface during installation. Feel free to post an issue on GitHub or contact the APDA tech committee if such issues occur.
4. At many instances throughout the guide, some terminal interaction will be required that is not strictly explained here, for example, typing "Y" to proceed with an installation or entering a root password. There are also various expected warnings, so don't panic when you see these during installation.
5. Especially due to the use of WSL, be aware that this setup usually takes up ~20 gb of disk space

## Step 1: Install WSL (Windows Only)

It is strongly recommended that Windows users install and develop through the Windows Subsystem for Linux (WSL). Open a terminal and install the default distribution (Ubuntu) by running:

```bash
wsl --install
```

Once WSL is installed, log in and create a password. If you are using VSCode, click the blue button in the bottom-left corner, select **WSL**, choose **Ubuntu**, and follow the prompts to enter a username and password. Other IDEs may have similar workflows, but it is strongly recommended to connect to WSL through your IDE.

---

## Step 2: Clone the Repository and Set Up MySQL

### Clone the Repository

```bash
git clone https://github.com/MIT-Tab/mit-tab.git
cd mit-tab
```

In addition to opening the `mit-tab` directory in your terminal, you should also open in with your IDE at this stage.

### Install Required Packages for MySql

```bash
sudo apt update
sudo apt install mysql-server libmysqlclient-dev
```
> **Note**: Make sure not to skip the `sudo apt update` command. Although in many other tutorials, this command is unnecessary, because we're on a new Ubuntu instance, it is strictly necessary for the rest of this tutorial to run.


### Configure MySQL

Log into the MySQL shell:

```bash
sudo mysql -u root -p
```

> **Note**: Since this is just a dev environment, security is unlikely to be a concern, so feel free to select a simple password (i.e. 123) that you won't forget.


Run the following commands to create the necessary database and user.

```sql
CREATE DATABASE mittab;
CREATE USER 'django'@'%' IDENTIFIED BY '123';
GRANT ALL PRIVILEGES ON *.* TO 'django'@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
\quit;
```
> **Note**: Swap out 123 if you want a more secure password.

### Configure Environment Variables

Create a file named `.env` in the project directory and copy the contents of `.env.example` into it. Modify the following line:

```env
MYSQL_USER=root
```
Change `root` to `django` and fill in the MySQL credentials with the password you set above.

---

## Step 3: Install Node Version Manager (NVM) for Node.js

Run the following command to install NVM:

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
```

> **Note**: The version number in this command may update frequently. Check the [NVM repository](https://github.com/nvm-sh/nvm) and copy the latest installation command from the "How to install" section in the README.

Restart your shell and install Node.js version 18:

```bash
nvm install 18
nvm use 18
```

---

## Step 4: Install Python and Required Tools

### Install Python and Dependencies

```bash
sudo apt-get install libffi-dev python3-venv python3-pip
```

### Install Pyenv

Install Pyenv with the following command:

```bash
curl -fsSL https://pyenv.run | bash
```

To run `pyenv` commands, you'll need to add it to your path file. You can do so with the below command, although the curl command above should output similar instructions on how to do this

```bash
export PYENV_ROOT="$HOME/.pyenv"
[[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init - bash)"
```

Restart your shell.

---

## Step 5: Set Up the Python Environment

### Install Python 3.7.13 Using Pyenv

```bash
pyenv install 3.7.13
pyenv local 3.7.13
```

### Set Up Virtual Environment

```bash
python -m venv venv
source venv/bin/activate
pip install pipenv
pipenv install --python 3.7
```
> **Note:** For unknown reasons, `pipenv install --python 3.7` occasionally fails the first time it is ran, but passes if ran a second time.

---

## Step 6: Install JavaScript Dependencies

Run the following command to install JavaScript dependencies:

```bash
npm install
```

---

## Step 7: Finalize and Run the Application

### Apply Migrations and Load Initial Data

```bash
pipenv run python manage.py migrate
pipenv run python manage.py loaddata testing_db
```

### Start the Development Server

```bash
pipenv run ./bin/dev-server
```

---

Your development environment is now set up and ready to use! 

## Step 8: Development

Lastly, here are some rough guidelines on development.

1. Linting:
The GitHub is configured to automatically run `PyLint` and check for errors. It is recommended that you install a code-formatter like `black` to take care of the more menial formatting details (especially whitespace and line length), and PyLint to check your code for linting errors before opening a PR. Integration with your IDE will vary, but if you'd like to use this just in terminal, you can do so with 

```bash
pip install black, pylint
```

2. Versions:
Some of the libraries and tools used in this repo may be somewhere between 5-10 years behind their current version. Keep this in mind when searching for documentation online since parsing what advice applies to what version can occasionally be a headache

3. Django ORM 
Django abstracts database operations through an API called the Object-Relational Mapper (ORM), meaning developers typically interact with the database through object-oriented programming instead of SQL queries. While this simplifies development, it makes it very easy to accidentally make thousands of database queries in relatively simple code.

You can avoid this by familiarizing yourself with the Django ORM and best practices for query optimization, such as preventing the `N+1` problem using methods like `prefetch_related`. Refer to guides like [this one](https://medium.com/@RohitPatil18/n-1-problem-in-django-and-solution-3f5307039c06) for further details. Additionally, enable [`Django Silk`](https://medium.com/@sharif-42/profiling-django-application-using-django-silk-62cdea83fb83) to profile your code, and make sure you're only making the queries you intend to.

4. Have fun! :)
