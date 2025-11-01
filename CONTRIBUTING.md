# Contributing to MIT-Tab

Thank you for your interest in contributing to MIT-Tab! This document provides guidelines and information for developers looking to contribute to the project.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Development Guidelines](#development-guidelines)
- [Submitting Changes](#submitting-changes)
- [Getting Help](#getting-help)

## Code of Conduct

We are committed to providing a welcoming and inclusive environment for all contributors. Please be respectful and professional in all interactions.

## Getting Started

MIT-Tab is a web application built with Django and used to manage APDA debate tournaments. Before contributing:

1. Familiarize yourself with [the user documentation](https://mit-tab.readthedocs.io/en/latest/) to understand how the application works
2. Set up your development environment following the [Development Setup](#development-setup) guide below
3. Check the [issues page](https://github.com/MIT-Tab/mit-tab/issues) for tasks that need help

## Development Setup

This guide will help you set up a local development environment for MIT-Tab. The setup process varies by operating system - expand the section for your platform below.

### Prerequisites

Before you begin, ensure you have:
- Git installed
- A text editor or IDE
- Basic familiarity with terminal/command line

### Platform-Specific Setup

<details>
<summary><b> macOS Setup</b></summary>

#### Step 1: Install Homebrew

If you don't already have a package manager, Homebrew is recommended for macOS. See [https://brew.sh/](https://brew.sh/) for installation instructions.

#### Step 2: Clone the Repository

```bash
git clone https://github.com/MIT-Tab/mit-tab.git
cd mit-tab
```

Open the `mit-tab` directory in your IDE.

#### Step 3: Install and Configure MySQL

Install MySQL using Homebrew:

```bash
brew install mysql
brew services start mysql
mysql_secure_installation
```

The `mysql_secure_installation` command will prompt you to set a root password and configure security settings.

Log into MySQL and create the database:

```bash
mysql -u root -p
```

Run these SQL commands to create the database and user for the application:

```sql
CREATE DATABASE mittab;
CREATE USER 'django'@'%' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON *.* TO 'django'@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
\quit;
```

#### Step 4: Configure Environment Variables

Copy the example environment file and update it with your MySQL credentials:

```bash
cp .env.example .env
```

Edit `.env` and update the MySQL settings to match what you set in Step 3.

#### Step 5: Install Node.js

Install NVM (Node Version Manager) to manage Node.js versions. This allows you to easily switch between Node versions for different projects. Installation instructions are available at the [NVM repository](https://github.com/nvm-sh/nvm).

Once NVM is installed, use it to install Node.js. The project may specify a required version in `.nvmrc`:

```bash
nvm install
nvm use
```

If there's no `.nvmrc` file, check `package.json` for the required Node version.

#### Step 6: Install Python

Install pyenv to manage Python versions. This is similar to NVM but for Python - it lets you install and switch between different Python versions. Installation instructions are available in the [pyenv documentation](https://github.com/pyenv/pyenv).

Once pyenv is installed, install the Python version required by the project (check the `Pipfile` for the required version):

```bash
pyenv install <version>
pyenv local <version>
```

The `local` command sets the Python version for this project directory.

#### Step 7: Set Up Python Environment

Install pipenv and the project dependencies:

```bash
pip install pipenv
pipenv install
```

If the install fails, try running it a second time.

#### Step 8: Install JavaScript Dependencies

```bash
npm install
```

#### Step 9: Initialize the Database

Run migrations and load test data:

```bash
pipenv run python manage.py migrate
pipenv run python manage.py loaddata testing_db
```

#### Step 10: Start the Development Server

```bash
pipenv run ./bin/dev-server
```

The application should now be running at `http://0.0.0.0:8001`

Default login credentials:
- **Username**: `tab`
- **Password**: `password`

</details>

<details>
<summary><b> Windows Setup (WSL)</b></summary>

#### Step 1: Install WSL

It is strongly recommended to use Windows Subsystem for Linux (WSL) for development on Windows. Install it by running this command in PowerShell or Command Prompt:

```bash
wsl --install
```

This will install Ubuntu by default. Once installed, create a username and password when prompted.

#### Step 2: Connect Your IDE to WSL

If using VS Code, install the "Remote - WSL" extension, then click the blue button in the bottom-left corner, select **WSL**, choose **Ubuntu**, and follow the prompts. This allows you to develop directly in the Linux environment.

#### Step 3: Continue with Linux Setup

Now that you have a Linux environment through WSL, follow the **Linux Setup (Debian/Ubuntu)** instructions below.

</details>

<details>
<summary><b> Linux Setup (Debian/Ubuntu)</b></summary>

#### Step 1: Clone the Repository

```bash
git clone https://github.com/MIT-Tab/mit-tab.git
cd mit-tab
```

Open the `mit-tab` directory in your IDE.

#### Step 2: Install and Configure MySQL

Update your package list and install MySQL:

```bash
sudo apt update
sudo apt install mysql-server libmysqlclient-dev
```

The `libmysqlclient-dev` package is needed to build the Python MySQL client.

Log into MySQL and create the database:

```bash
sudo mysql -u root -p
```

Run these SQL commands to create the database and user for the application:

```sql
CREATE DATABASE mittab;
CREATE USER 'django'@'%' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON *.* TO 'django'@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
\quit;
```

#### Step 3: Configure Environment Variables

Copy the example environment file and update it with your MySQL credentials:

```bash
cp .env.example .env
```

Edit `.env` and update the MySQL settings to match what you set in Step 2.

#### Step 4: Install Node.js

Install NVM (Node Version Manager) to manage Node.js versions. This allows you to easily switch between Node versions for different projects. Installation instructions are available at the [NVM repository](https://github.com/nvm-sh/nvm).

Once NVM is installed, use it to install Node.js. The project may specify a required version in `.nvmrc`:

```bash
nvm install
nvm use
```

If there's no `.nvmrc` file, check `package.json` for the required Node version.

#### Step 5: Install Python

First, install dependencies needed to build Python:

```bash
sudo apt-get install libffi-dev python3-venv python3-pip
```

Install pyenv to manage Python versions. This is similar to NVM but for Python - it lets you install and switch between different Python versions. Installation instructions are available in the [pyenv documentation](https://github.com/pyenv/pyenv).

Once pyenv is installed, install the Python version required by the project (check the `Pipfile` for the required version):

```bash
pyenv install <version>
pyenv local <version>
```

The `local` command sets the Python version for this project directory.

#### Step 6: Set Up Python Environment

Install pipenv and the project dependencies:

```bash
pip install pipenv
pipenv install
```

If the install fails, try running it a second time.

#### Step 7: Install JavaScript Dependencies

```bash
npm install
```

#### Step 8: Initialize the Database

Run migrations and load test data:

```bash
pipenv run python manage.py migrate
pipenv run python manage.py loaddata testing_db
```

#### Step 9: Start the Development Server

```bash
pipenv run ./bin/dev-server
```

The application should now be running at `http://0.0.0.0:8001`

Default login credentials:
- **Username**: `tab`
- **Password**: `password`

</details>

### Alternative: Docker Setup

If you prefer to use Docker:

```bash
docker-compose build
docker-compose up
docker-compose run --rm web ./bin/setup password
```

Access the application at `http://localhost`.

Note: Docker simulates the production environment, which can be less convenient for active development compared to the local setup above.

## Development Guidelines

### Code Quality

#### Linting and Formatting

The project uses PyLint for linting. Before submitting a PR, format and check your code:

```bash
pip install black pylint
black .
pylint your_changed_files.py
```

The CI pipeline automatically runs PyLint checks on all PRs.

#### Testing

Before submitting a PR:
- Run tests to ensure existing functionality still works
- Add tests for new features you implement
- Chrome's headless driver is required for testing ([installation info](http://chromedriver.chromium.org/getting-started))

### Technology Stack

#### Library Versions

Some libraries in this project may be several years behind their current versions. When searching for documentation:
- Pay careful attention to version numbers
- Check the `Pipfile` and `package.json` for exact versions
- Older documentation may be more relevant than the latest guides

#### Django ORM Best Practices

Django abstracts database operations through its Object-Relational Mapper (ORM). While convenient, it's easy to accidentally create performance issues:

**Avoid the N+1 Problem:**
- Use `prefetch_related()` and `select_related()` to optimize queries
- Read this [guide on N+1 problems in Django](https://medium.com/@RohitPatil18/n-1-problem-in-django-and-solution-3f5307039c06)

**Profile Your Code:**
- Enable [Django Silk](https://medium.com/@sharif-42/profiling-django-application-using-django-silk-62cdea83fb83) to monitor database queries
- Verify you're only making the queries you intend to make
- Aim to minimize the number of database queries per request

### Code Style

- Follow PEP 8 guidelines for Python code
- Use meaningful variable and function names
- Add comments for complex logic
- Write docstrings for public functions and classes

## Submitting Changes

### Pull Request Process

1. **Create a feature branch:**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes:**
   - Write clean, well-documented code
   - Add or update tests as needed
   - Ensure all tests pass
   - Run linting tools

3. **Commit your changes:**
   ```bash
   git add .
   git commit -m "Description of your changes"
   ```

4. **Push to your fork:**
   ```bash
   git push origin feature/your-feature-name
   ```

5. **Open a Pull Request:**
   - Provide a clear description of the changes
   - Reference any related issues
   - Ensure CI checks pass

### Pull Request Guidelines

- Keep PRs focused on a single feature or fix
- Include tests for new functionality
- Update documentation if needed
- Respond to review feedback promptly
- Ensure your code passes all CI checks

## Getting Help

- **Issues:** Post questions or report bugs on the [GitHub Issues page](https://github.com/MIT-Tab/mit-tab/issues)
- **APDA Tech Committee:** Contact the APDA tech committee for project-specific questions
- **Documentation:** Check the [user documentation](https://mit-tab.readthedocs.io/en/latest/) for tournament-related questions

---

Thank you for contributing to MIT-Tab! Your efforts help make debate tournaments run more smoothly.
