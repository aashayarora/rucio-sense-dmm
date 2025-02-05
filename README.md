# Data Movement Manager (DMM)

Data Movement Manager (DMM) for the Rucio-SENSE interoperation prototype.
DMM is the interface between Rucio (/FTS) and SENSE, making SDN operated HEP data-flows possible

The application is organized into several components that run as daemons on separate threads and evaluate requests based on their status in DMM, e.g. the `SENSE Stager Daemon` only operates on requests which are in the `DECIDED` state etc.

## Project Structure

- **daemons/**  
  Contains various background worker processes (daemons) that handle operations such as decision making, monitoring, and interfacing with external systems.
  - **core/** – Daemons related to core operations (e.g. allocation, decision, monitoring, refreshing site information). See [`daemons/core/monit.py`](daemons/core/monit.py) for the monitoring daemon and [`daemons/core/decider.py`](daemons/core/decider.py) for decision logic.
  - **fts/** – Daemons for managing FTS (File Transfer Service) configurations and modifications. For example, see [`daemons/fts/modifier.py`](daemons/fts/modifier.py).
  - **rucio/** – Daemons to initialize and modify Rucio rules, such as in [`daemons/rucio/initializer.py`](daemons/rucio/initializer.py).
  - **sense/** – Daemons managing SENSE operations. This includes several processes:  
    - Handler: [`daemons/sense/handler.py`](daemons/sense/handler.py)  
    - Canceller: [`daemons/sense/canceller.py`](daemons/sense/canceller.py)  
    - Additional SENSE related daemons (e.g. provisioner, stager, modifier) are organized similarly.

- **db/**  
  Contains database models and session management. Key models include:
  - Site, Request, Endpoint, Mesh. For example, see [`db/site.py`](db/site.py) and [`db/request.py`](db/request.py).

- **frontend/**  
  Implements a FastAPI web frontend that serves templates and static files.
  - The FastAPI application is defined in [`frontend/frontend.py`](frontend/frontend.py).
  - HTML templates are stored in the [`frontend/templates`](frontend/templates) folder (e.g. [`frontend/templates/index.html`](frontend/templates/index.html) and [`frontend/templates/sites.html`](frontend/templates/sites.html)).
  - Static assets such as CSS are located in [`frontend/static`](frontend/static) (e.g. [`frontend/static/styles.css`](frontend/static/styles.css)).

- **main/**  
  Contains the main startup logic where various daemon instances are created and the web frontend is launched. See [`main/dmm.py`](main/dmm.py).

- **utils/**  
  Provides utility functions and configuration management. For example:
  - Configuration parsing is implemented in [`utils/config.py`](utils/config.py).
  - Additional utilities are located in modules such as [`utils/monit.py`](utils/monit.py).

## Setup
### Running in Kubernetes (Recommended)
1. Create Configuration Secrets (see etc/mksecrets.sh)
2. Create Deployment
```
kubectl apply -f etc/deploy.yaml
```