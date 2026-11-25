# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/CalixteManaud/jobwatch/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                          |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|------------------------------ | -------: | -------: | -------: | -------: | ------: | --------: |
| src/jobwatch/\_\_init\_\_.py  |        4 |        0 |        0 |        0 |    100% |           |
| src/jobwatch/\_\_main\_\_.py  |        1 |        1 |        0 |        0 |      0% |         3 |
| src/jobwatch/anonymize.py     |       51 |        1 |       26 |        1 |     97% |        54 |
| src/jobwatch/cli.py           |      169 |        3 |       22 |        1 |     98% |247-\>249, 291-293 |
| src/jobwatch/collect.py       |       46 |        0 |        4 |        0 |    100% |           |
| src/jobwatch/config.py        |       96 |        1 |       18 |        1 |     98% |        59 |
| src/jobwatch/digest.py        |       52 |        0 |        4 |        0 |    100% |           |
| src/jobwatch/discord.py       |      121 |        0 |       30 |        2 |     99% |107-\>109, 178-\>exit |
| src/jobwatch/errors.py        |       15 |        0 |        0 |        0 |    100% |           |
| src/jobwatch/exporters.py     |       59 |        0 |       12 |        0 |    100% |           |
| src/jobwatch/francetravail.py |      219 |        2 |       58 |        3 |     98% |   86, 334 |
| src/jobwatch/log.py           |       31 |        0 |       10 |        0 |    100% |           |
| src/jobwatch/metrics.py       |       50 |        0 |        6 |        1 |     98% | 103-\>105 |
| src/jobwatch/models.py        |       79 |        1 |        6 |        1 |     98% |        40 |
| src/jobwatch/pipeline.py      |       62 |        0 |        4 |        0 |    100% |           |
| src/jobwatch/ratelimit.py     |       19 |        0 |        4 |        0 |    100% |           |
| src/jobwatch/retry.py         |       53 |        0 |        6 |        1 |     98% |   73-\>77 |
| src/jobwatch/scoring.py       |      110 |        0 |       28 |        0 |    100% |           |
| src/jobwatch/settings.py      |       39 |        0 |        8 |        0 |    100% |           |
| src/jobwatch/store.py         |      101 |        0 |       18 |        0 |    100% |           |
| **TOTAL**                     | **1377** |    **9** |  **264** |   **11** | **99%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/CalixteManaud/jobwatch/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/CalixteManaud/jobwatch/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/CalixteManaud/jobwatch/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/CalixteManaud/jobwatch/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2FCalixteManaud%2Fjobwatch%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/CalixteManaud/jobwatch/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.