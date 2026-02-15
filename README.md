# RNA-seq Analysis Web App (Flask + R)

This is a Flask-based RNA-seq analysis web app with R-powered pipelines for:

- DEG (Differential Expression)
- Pathway Enrichment
- ID2Symbol (Gene ID mapping)
- ssGSEA

UI routes:

- `/` (home)
- `/deg`
- `/pathway`
- `/id2symbol`
- `/ssgsea`

API routes (mounted under `/api`):

- `/api/deg`
- `/api/pathway`
- `/api/id2symbol`
- `/api/ssgsea`

## Inputs and Formats

- DEG: count matrix with gene names in column 1 and samples in remaining columns
- Pathway: preranked gene list (2 columns: gene, score) in `.tsv`, `.txt`, or `.csv`
- ID2Symbol: gene ID list in `.tsv`, `.txt`, or `.csv`
- ssGSEA: expression matrix in `.tsv`, `.txt`, or `.csv`, plus a `.gmt` file

Uploads are validated server-side and stored in a non-web folder (`.data/uploads`) and cleaned up after analysis.

## Local Development

1. Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

2. Install Python dependencies:

```bash
pip install -r requirements.txt
```

3. Run locally (Flask dev server, not for production):

```bash
export FLASK_RUN_HOST=127.0.0.1
export FLASK_DEBUG=1
python app.py
```

Production should use gunicorn:

```bash
gunicorn -w 2 -b 127.0.0.1:8000 app:app
```

## AWS EC2 Deployment (Ubuntu 22.04)

### 1) System dependencies

```bash
sudo apt update
sudo apt install -y python3-venv nginx git r-base
```

### 2) Clone the repo

```bash
git clone <your-github-repo-url> RsAT
cd RsAT
```

### 3) Python environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4) R packages

Install R packages used by the scripts (run in R):

```r
install.packages(c("BiocManager"))
BiocManager::install(c("edgeR", "DESeq2", "GSVA", "org.Hs.eg.db", "AnnotationDbi"))
```

### 5) Systemd service

Copy the service file and edit paths if needed:

```bash
sudo cp deploy/rnatools.service /etc/systemd/system/rnatools.service
sudo nano /etc/systemd/system/rnatools.service
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable rnatools
sudo systemctl start rnatools
sudo systemctl status rnatools
```

### 6) Nginx reverse proxy

```bash
sudo cp deploy/nginx_rnatools.conf /etc/nginx/sites-available/rnatools
sudo ln -s /etc/nginx/sites-available/rnatools /etc/nginx/sites-enabled/rnatools
sudo nginx -t
sudo systemctl restart nginx
```

### 7) Security group

In AWS EC2 Security Group, allow:

- Inbound TCP 80 (HTTP)
- Inbound TCP 443 (HTTPS, if using TLS)

### 8) Update process

```bash
cd /home/ubuntu/RsAT
git pull
sudo systemctl restart rnatools
```

## Notes

- No user authentication is included.
- Do not commit secrets. Use `.env` locally and keep it out of git.
- The Flask dev server is only for local testing. Use gunicorn in production.
