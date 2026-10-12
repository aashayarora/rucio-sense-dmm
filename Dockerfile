FROM python:3.11-slim-bookworm

RUN apt update && \
    apt-get install --yes --no-install-recommends tini && \
    apt-get clean autoclean && \
    apt-get autoremove --yes && \
    rm -rf /var/lib/{apt,dpkg,cache,log}/

RUN pip3 install GitPython==3.1.46 MarkupSafe==3.0.3 SQLAlchemy==2.0.46 annotated-doc==0.0.4 annotated-types==0.7.0 anyio==4.12.1 attrs==25.4.0 certifi==2026.1.4 charset_normalizer==3.4.4 click==8.3.1 decorator==5.2.1 dmm==0.0.1 dogpile-cache==1.5.0 fastapi==0.128.0 gitdb==4.0.12 greenlet==3.3.1 h11==0.16.0 idna==3.11 iniconfig==2.3.0 jinja2==3.1.6 jsonpath_ng==1.7.0 jsonschema==4.26.0 jsonschema-specifications==2025.9.1 markdown-it-py==4.0.0 mdurl==0.1.2 networkx==3.6.1 numpy==2.4.1 packaging==26.0 pluggy==1.6.0 ply==3.11 psycopg2-binary==2.9.11 pydantic==2.12.5 pydantic-core==2.41.5 pygments==2.19.2 pytest==9.0.2 pyyaml==6.0.3 referencing==0.37.0 requests==2.32.5 rich==14.3.1 rpds-py==0.30.0 rucio-clients==39.1.0 scipy==1.17.0 sense-o-api==1.53 six==1.17.0 smmap==5.0.2 sqlmodel==0.0.31 starlette==0.50.0 stevedore==5.6.0 tabulate==0.9.0 typing-extensions==4.15.0 typing-inspection==0.4.2 urllib3==2.6.3 uvicorn==0.40.0 zipp==3.23.0

COPY . /opt/dmm/
RUN pip3 install --no-cache-dir /opt/dmm/

COPY ./docker/wait-for-it.sh /wait-for-it.sh
COPY ./docker/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /wait-for-it.sh /docker-entrypoint.sh

ENV PYTHONPATH=/opt/dmm/
ENV DMM_CONFIG=/opt/dmm/dmm.cfg
ENV PYTHONUNBUFFERED=1

EXPOSE 80

ENTRYPOINT ["tini", "-g", "--", "/docker-entrypoint.sh"]
