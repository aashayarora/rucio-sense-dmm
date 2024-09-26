FROM python:3.7-slim-bullseye

RUN apt update && apt install sqlite3
RUN apt-get clean autoclean && \
    apt-get autoremove --yes && \
    rm -rf /var/lib/{apt,dpkg,cache,log}/

RUN pip3 install zipp waitress urllib3 typing-extensions tomli tabulate smmap six pyyaml pyrsistent psycopg2-binary pbr packaging numpy networkx MarkupSafe itsdangerous iniconfig idna greenlet exceptiongroup decorator charset-normalizer certifi Werkzeug scipy requests Jinja2 importlib-metadata gitdb stevedore sqlalchemy pluggy GitPython click attrs pytest jsonschema flask dogpile.cache sense-o-api rucio-clients

COPY . /opt/dmm/
RUN pip3 install /opt/dmm/

COPY ./docker/wait-for-it.sh /wait-for-it.sh
COPY ./docker/docker-entrypoint.sh /docker-entrypoint.sh

ENV PYTHONPATH=/opt/dmm/
ENV DMM_CONFIG /opt/dmm/dmm.cfg

EXPOSE 80

ENTRYPOINT ["/docker-entrypoint.sh"]
