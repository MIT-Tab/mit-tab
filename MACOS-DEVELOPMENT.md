# Guide to Setting Up the Development Environment

This is a guide to getting a minimum viable **software development** setup of MIT-Tab on MacOS. If you're a tournament or tab director looking for help on how to use mit-tab, please reference [the docs](https://mit-tab.readthedocs.io/en/latest/) instead.


## Preliminary notes and Disclaimers
1. This guide is meant to get new devs off the ground as quickly as possible, so it is deliberately opinionated, and there are plenty of other ways to reach similar outcomes.
2. This guide assumes surface-level familiarity with software development, using IDEs, and bash commands. Specific familiarity with these libraries, technologies, and package managers is not strictly required, but may become needed if unexpected issues surface during installation. Feel free to post an issue on GitHub or contact the APDA tech committee if such issues occur.
3. At many instances throughout the guide, some terminal interaction will be required that is not strictly explained here, for example, typing "Y" to proceed with an installation or entering a root password. There are also various expected warnings, so don't panic when you see these during installation.

## Step 1: Install Brew

It is strongly recommended that MacOS users install brew to assist with package management. Open a terminal and install brew by running:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

More information on brew can be found [here](https://brew.sh/).

## Step 2: Clone the Repository and Set Up MySQL

### Clone the Repository

```bash
git clone https://github.com/MIT-Tab/mit-tab.git
cd mit-tab
```

In addition to opening the `mit-tab` directory in your terminal, you should also open in with your IDE at this stage.

### Install MySQL and Enable Startup Service

```bash
brew install mysql
brew services start mysql
```

### Configure MySQL

By default, MySQL is installed with a root user and no password. Run the following command to secure the installation. The default settings are generally fine for a development environment.

```bash
mysql_secure_installation
```

Log into the MySQL shell with the password you set during the installation:

```bash
mysql -u root -p
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

Create a file named `.env` in the project directory and copy the contents of `.env.example` into it:
```bash
cp .env.example .env
```

Modify the following line:

```env
MYSQL_USER=root
```
Change `root` to `django` and fill in the MySQL credentials with the passwords you set above in the `.env` file:
```env
MYSQL_PASSWORD=
MYSQL_ROOT_PASSWORD=
```
---

## Step 3: Install Node Version Manager (NVM) for Node.js

Run the following command to install NVM:

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
```

> **Note**: The version number in this command may update frequently. Check the [NVM repository](https://github.com/nvm-sh/nvm) and copy the latest installation command from the "How to install" section in the README. We don't recommend using the `brew` installation method for NVM.

Restart your shell and `cd` to the repo or run the following command to re-init nvm:
```bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # This loads nvm bash_completion
```

Install Node.js version 18:

```bash
nvm install 18
nvm use 18
```

---

## Step 4: Install Python and Required Tools

### Install Python and Dependencies

Install python3 with the following command:
```bash
brew install python3
```
> **Note**: This may already be installed on your system, but it's good to check.

### Install Pyenv

Install Pyenv with the following command:

```bash
brew install pyenv
```

To run `pyenv` commands, you'll need to add it to your path file. You can do so with the below command if using ZSH, although the brew command above should output similar instructions on how to do this. 

```bash
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.zshrc
echo '[[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.zshrc
echo 'eval "$(pyenv init - zsh)"' >> ~/.zshrc
```

> **Note**: If you're using a different shell, guidance on how to add `pyenv` to your path can be found in the [pyenv documentation](https://github.com/pyenv/pyenv?tab=readme-ov-file#b-set-up-your-shell-environment-for-pyenv).

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

The development server should now be running at `http://0.0.0.0:8001`. The default login credentials are `tab` and `password`.

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
