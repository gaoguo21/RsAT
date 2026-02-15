#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 4) {
  stop("Usage: Rscript ssgsea.R <expr_path> <gmt_path> <output_path> <summary_path>")
}

expr_path <- args[1]
gmt_path <- args[2]
output_path <- args[3]
summary_path <- args[4]

if (!requireNamespace("GSVA", quietly = TRUE)) stop("GSVA package is required.")
if (!requireNamespace("GSEABase", quietly = TRUE)) stop("GSEABase package is required.")
options(matrixStats.useNames = TRUE)

if (requireNamespace("matrixStats", quietly = TRUE)) {
  patch_useNames <- function(fname) {
    ns <- asNamespace("matrixStats")
    if (!exists(fname, envir = ns, inherits = FALSE)) return(invisible(NULL))
    f <- get(fname, envir = ns)
    if (!"useNames" %in% names(formals(f))) return(invisible(NULL))
    unlockBinding(fname, ns)
    assign(
      fname,
      function(..., useNames = TRUE) {
        if (is.null(useNames) || is.na(useNames)) useNames <- TRUE
        f(..., useNames = useNames)
      },
      envir = ns
    )
    lockBinding(fname, ns)
    invisible(NULL)
  }
  
  for (fn in c(
    "rowRanks", "colRanks", "rowSums2", "colSums2",
    "rowMeans2", "colMeans2", "rowVars", "colVars",
    "rowMins", "rowMaxs"
  )) {
    patch_useNames(fn)
  }
}

read_expr <- function(path) {
  # allow .zip containing a single matrix file
  if (grepl("\\.zip$", path, ignore.case = TRUE)) {
    tmpdir <- tempfile("ssgsea_unzip_")
    dir.create(tmpdir)
    utils::unzip(path, exdir = tmpdir)
    files <- list.files(tmpdir, full.names = TRUE)
    if (length(files) == 0) stop("ZIP file is empty.")
    path <- files[1]
  }
  
  # prefer data.table::fread if available (faster + supports .gz)
  use_fread <- requireNamespace("data.table", quietly = TRUE)
  
  is_csv <- grepl("\\.csv(\\.gz)?$", path, ignore.case = TRUE)
  is_tsv <- grepl("\\.tsv(\\.gz)?$", path, ignore.case = TRUE) ||
    grepl("\\.txt(\\.gz)?$", path, ignore.case = TRUE)
  
  if (!is_csv && !is_tsv) {
    stop("Invalid file type. Use .tsv/.tsv.gz, .csv/.csv.gz, or .zip only.")
  }
  
  if (use_fread) {
    sep <- if (is_csv) "," else "\t"
    df <- data.table::fread(path, sep = sep, data.table = FALSE, check.names = TRUE)
  } else {
    if (is_csv) {
      df <- read.csv(path, stringsAsFactors = FALSE, check.names = TRUE)
    } else {
      df <- read.table(path, header = TRUE, sep = "\t", stringsAsFactors = FALSE, check.names = TRUE)
    }
  }
  
  if (ncol(df) < 2) stop("Expression matrix must have at least 2 columns: gene + samples.")
  
  genes <- as.character(df[[1]])
  mat <- as.matrix(df[, -1, drop = FALSE])
  
  # coerce numeric safely
  suppressWarnings(storage.mode(mat) <- "numeric")
  if (anyNA(mat)) {
    stop("Expression matrix contains non-numeric values (NA introduced during numeric conversion).")
  }
  
  rownames(mat) <- genes
  mat
}

expr <- read_expr(expr_path)

# drop empty gene names
expr <- expr[!is.na(rownames(expr)) & rownames(expr) != "", , drop = FALSE]

# collapse duplicate genes (mean)
if (any(duplicated(rownames(expr)))) {
  df_expr <- data.frame(Gene = rownames(expr), expr, check.names = FALSE)
  df_expr <- stats::aggregate(. ~ Gene, data = df_expr, FUN = mean)
  rownames(df_expr) <- df_expr$Gene
  expr <- as.matrix(df_expr[, -1, drop = FALSE])
  storage.mode(expr) <- "numeric"
}

# read GMT
gmt <- GSEABase::getGmt(gmt_path)
gene_sets <- GSEABase::geneIds(gmt)

total_sets <- length(gene_sets)
low_overlap_sets <- 0
if (total_sets > 0) {
  overlaps <- vapply(gene_sets, function(gs) sum(gs %in% rownames(expr)), integer(1))
  low_overlap_sets <- sum(overlaps < 5)
}

# ---- RUN ssGSEA (robust across GSVA versions) ----
# ---- RUN ssGSEA (robust across GSVA versions) ----
scores <- tryCatch(
  {
    # Legacy interface (GSVA <= 1.44-ish)
    GSVA::gsva(expr, gene_sets, method = "ssgsea", verbose = FALSE)
  },
  error = function(e_old) {
    # If "method" isn't supported, try newer parameter-object API
    if (grepl("unused argument.*method|argument.*method", e_old$message, ignore.case = TRUE)) {
      
      if (!exists("ssgseaParam", envir = asNamespace("GSVA"), inherits = TRUE)) {
        stop(
          paste0(
            "GSVA does not support legacy method='ssgsea' and 'ssgseaParam' was not found. ",
            "Installed GSVA version: ", as.character(utils::packageVersion("GSVA")), ". ",
            "Please update GSVA from Bioconductor."
          )
        )
      }
      
      param <- GSVA::ssgseaParam(exprData = expr, geneSets = gene_sets)
      return(GSVA::gsva(param, verbose = FALSE))
    }
    
    stop(
      paste0(
        "GSVA ssGSEA failed. GSVA version: ",
        as.character(utils::packageVersion("GSVA")),
        ". Error: ", e_old$message
      )
    )
  }
)
# -----------------------------------------------

# -----------------------------------------------

write.csv(scores, output_path)

summary_lines <- c(
  paste0("low_overlap_sets=", low_overlap_sets),
  paste0("total_sets=", total_sets)
)
writeLines(summary_lines, summary_path)
