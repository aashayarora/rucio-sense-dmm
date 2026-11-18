# Data Movement Manager (DMM)

Data Movement Manager (DMM) provides the interface layer between Rucio/FTS and SENSE for the Rucio-SENSE interoperation framework. 
DMM enables Software-Defined Networking (SDN) operated high-energy physics (HEP) data flows by orchestrating network-aware data transfers.

## Prerequisites

- **Rucio Server**: Must be running and accessible (with DMM patch applied)
- **PostgreSQL**: Must be running and reachable by DMM.
- **Certificates**: Valid X.509 host certificates for authentication with FTS.
- **SENSE OAuth**: Configured `.sense-o-auth.yaml` credentials
- **Kubernetes**: (for Kubernetes deployment, NRP is recommended)
- **Docker**: (for Docker deployment)

## Configuration

DMM requires the following configuration files:

1. **`dmm.cfg`**: DMM-specific configuration, refer to [`docs/configuration.md`](docs/configuration.md) for an example.
2. **`rucio.cfg`**: Rucio client configuration
3. **X.509 Certificates**: Host certificate and key (`hostcert.pem`, `hostcert.key.pem`)
4. **`.sense-o-auth.yaml`**: SENSE OAuth credentials

## Deployment

### Option 1: Kubernetes (Recommended for Production)

#### 1. Create Configuration Secrets

```bash
cd etc/
./mksecrets.sh
```

#### 3. Initialize RSEs in Rucio

Ensure all required Rucio Storage Elements (RSEs) are configured before deploying DMM. If RSEs are added later, use the "Refresh Sites" button in the DMM web interface (Sites tab).

#### 4. Deploy to Kubernetes using Helm

```bash
helm install dmm etc/helm/
```

The deployment includes an embedded PostgreSQL instance. For production environments, consider using an external managed database service.

### Option 2: Docker

#### 1. Start PostgreSQL

```bash
docker run -d \
  --name dmm-postgres \
  -e POSTGRES_PASSWORD=your_secure_password \
  -e POSTGRES_DB=dmm \
  -p 5432:5432 \
  postgres:14
```

#### 2. Run DMM Container

```bash
docker run -d \
  --name dmm \
  -v $HOME/private/dmm.cfg:/opt/dmm/dmm.cfg \
  -v $HOME/private/rucio.cfg:/opt/rucio/etc/rucio.cfg \
  -v $HOME/private/certs/rucio-sense/hostcert.pem:/opt/certs/cert.pem \
  -v $HOME/private/certs/rucio-sense/hostcert.key.pem:/opt/certs/key.pem \
  -v $HOME/.sense-o-auth.yaml:/root/.sense-o-auth.yaml \
  aaarora/dmm:latest
```

## Monitoring and Management

Access the DMM web frontend to monitor data flows and manage site configurations. The interface provides:

- Real-time transfer status monitoring
- Site/RSE management (with refresh capability)
- Network provisioning status