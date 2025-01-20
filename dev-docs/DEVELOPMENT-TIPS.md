Below are some rough guidelines on the basics of mit-tab development

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