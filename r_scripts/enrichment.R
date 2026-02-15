#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 4) {
  stop("Usage: Rscript enrichment.R <input_path> <output_path> <organism> <library> [gmt_path]")
}

input_path <- args[1]
output_path <- args[2]
organism <- args[3]
library <- tolower(args[4])
gmt_path <- ifelse(length(args) >= 5, args[5], "")

read_ranked <- function(path) {
  df <- NULL
  if (grepl("\\.xlsx?$", path, ignore.case = TRUE)) {
    if (!requireNamespace("readxl", quietly = TRUE)) {
      stop("readxl package is required for Excel input.")
    }
    df <- readxl::read_excel(path)
  } else if (grepl("\\.csv$", path, ignore.case = TRUE)) {
    df <- tryCatch(
      read.csv(path, stringsAsFactors = FALSE),
      error = function(e) read.table(path, header = TRUE, sep = ",", stringsAsFactors = FALSE)
    )
  } else {
    df <- read.table(path, header = TRUE, stringsAsFactors = FALSE)
  }

  if (ncol(df) < 2) {
    df <- tryCatch(
      read.table(path, header = FALSE, stringsAsFactors = FALSE),
      error = function(e) df
    )
  }
  if (ncol(df) < 2) {
    stop("Input file must have at least 2 columns: gene and fold-change.")
  }

  genes <- as.character(df[[1]])
  fc <- suppressWarnings(as.numeric(df[[2]]))
  if (any(is.na(fc))) {
    stop("Second column must be numeric fold-change values.")
  }
  list(genes = genes, fc = fc)
}

if (!requireNamespace("fgsea", quietly = TRUE)) stop("fgsea package is required.")
if (!requireNamespace("msigdbr", quietly = TRUE)) stop("msigdbr package is required.")

species <- ifelse(tolower(organism) == "mouse", "Mus musculus", "Homo sapiens")

# --- Helper: msigdbr call compatible with both old and new APIs ---
msigdbr_fetch <- function(species, collection, subcollection = NULL) {
  fmls <- names(formals(msigdbr::msigdbr))

  if ("collection" %in% fmls) {
    # msigdbr "modern" API
    if (is.null(subcollection)) {
      return(msigdbr::msigdbr(species = species, collection = collection))
    } else {
      return(msigdbr::msigdbr(species = species, collection = collection, subcollection = subcollection))
    }
  } else {
    # msigdbr legacy API
    if (is.null(subcollection)) {
      return(msigdbr::msigdbr(species = species, category = collection))
    } else {
      return(msigdbr::msigdbr(species = species, category = collection, subcategory = subcollection))
    }
  }
}

# --- Helper: find the right subcollection string for this msigdbr version ---
pick_subcollection <- function(target_lib) {
  cols <- msigdbr::msigdbr_collections()
  cols <- as.data.frame(cols, stringsAsFactors = FALSE)

  pick_first <- function(candidates, nms) {
    hit <- candidates[candidates %in% nms]
    if (length(hit) == 0) return(NULL)
    hit[1]
  }

  nms <- names(cols)

  # supports multiple msigdbr schemas, including:
  # gs_collection/gs_subcollection (your version)
  col_name <- pick_first(
    c("gs_collection", "collection", "category", "gs_cat", "gs_category"),
    nms
  )
  sub_name <- pick_first(
    c("gs_subcollection", "subcollection", "subcategory", "gs_subcat", "gs_subcategory"),
    nms
  )

  if (is.null(col_name) || is.null(sub_name)) {
    stop(
      paste0(
        "msigdbr_collections() has unexpected column names: ",
        paste(nms, collapse = ", "),
        ". Please update pick_subcollection() mapping for this msigdbr version."
      )
    )
  }

  cols[[col_name]] <- as.character(cols[[col_name]])
  cols[[sub_name]] <- as.character(cols[[sub_name]])

  # We want C2 (Curated gene sets) subcollections
  c2 <- cols[cols[[col_name]] == "C2", , drop = FALSE]
  if (nrow(c2) == 0) {
    stop("Could not find collection/category 'C2' in msigdbr_collections().")
  }

  pattern <- switch(
    target_lib,
    "kegg" = "KEGG",
    "reactome" = "REACTOME",
    "biocarta" = "BIOCARTA",
    stop("Unsupported subcollection target.")
  )

  hits <- c2[grepl(pattern, c2[[sub_name]], ignore.case = TRUE), , drop = FALSE]
  if (nrow(hits) == 0) {
    stop(
      paste0(
        "Could not find a ", pattern, " subcollection under C2 in msigdbr_collections(). ",
        "Available C2 subcollections include: ",
        paste(unique(c2[[sub_name]]), collapse = ", ")
      )
    )
  }

  as.character(hits[[sub_name]][1])
}

get_pathways <- function(lib, gmt_path, species) {
  if (lib == "custom") {
    if (nchar(gmt_path) == 0 || !file.exists(gmt_path)) {
      stop("Custom dataset selected but GMT file missing.")
    }
    return(fgsea::gmtPathways(gmt_path))
  }

  if (lib == "hallmark") {
    msig <- msigdbr_fetch(species = species, collection = "H")
  } else if (lib == "kegg") {
    subc <- pick_subcollection("kegg")
    msig <- msigdbr_fetch(species = species, collection = "C2", subcollection = subc)
  } else if (lib == "reactome") {
    subc <- pick_subcollection("reactome")
    msig <- msigdbr_fetch(species = species, collection = "C2", subcollection = subc)
  } else if (lib == "biocarta") {
    subc <- pick_subcollection("biocarta")
    msig <- msigdbr_fetch(species = species, collection = "C2", subcollection = subc)
  } else if (lib == "go") {
    msig <- msigdbr_fetch(species = species, collection = "C5")
  } else {
    stop("Unsupported dataset. Use one of: hallmark, kegg, reactome, biocarta, go, custom.")
  }

  if (!all(c("gene_symbol", "gs_name") %in% names(msig))) {
    stop("msigdbr output is missing expected columns (gene_symbol, gs_name).")
  }

  split(msig$gene_symbol, msig$gs_name)
}

# ---------------- Main ----------------

ranked <- read_ranked(input_path)
if (length(ranked$genes) == 0) stop("No genes found in input.")

pathways <- get_pathways(library, gmt_path, species)

stats_df <- data.frame(gene = ranked$genes, score = ranked$fc, stringsAsFactors = FALSE)
stats_df <- stats_df[!is.na(stats_df$gene) & !is.na(stats_df$score), ]

# Collapse duplicates: keep the score with largest absolute magnitude per gene
stats_df <- aggregate(score ~ gene, data = stats_df, FUN = function(x) x[which.max(abs(x))])

# Sort decreasing so enrichment direction is consistent
stats_df <- stats_df[order(-stats_df$score), ]

stats <- stats_df$score
names(stats) <- stats_df$gene

fgsea_res <- tryCatch(
  fgsea::fgseaMultilevel(pathways = pathways, stats = stats),
  error = function(e) fgsea::fgsea(pathways = pathways, stats = stats)
)

# ---- FILTER LOW-OVERLAP (LIKELY FALSE POSITIVES) ----
min_report_size <- 5

small <- fgsea_res[fgsea_res$size < min_report_size, , drop = FALSE]
if (nrow(small) > 0) {
  warning(sprintf(
    "Filtered out %d pathway(s) with size < %d (low gene overlap; likely unstable/false positives).",
    nrow(small), min_report_size
  ))
  fgsea_res <- fgsea_res[fgsea_res$size >= min_report_size, , drop = FALSE]
}

if (nrow(fgsea_res) == 0) {
  stop(sprintf(
    "No pathways passed the minimum overlap filter (size >= %d). Try lowering min_report_size or providing a longer ranked gene list.",
    min_report_size
  ))
}
# ----------------------------------------------------

fgsea_res <- fgsea_res[order(fgsea_res$padj, fgsea_res$pval), ]

leading_edge <- sapply(fgsea_res$leadingEdge, function(x) paste(x, collapse = ";"))

result <- data.frame(
  pathway = fgsea_res$pathway,
  NES = fgsea_res$NES,
  pval = fgsea_res$pval,
  padj = fgsea_res$padj,
  size = fgsea_res$size,
  leadingEdge = leading_edge,
  genes = leading_edge,
  stringsAsFactors = FALSE
)

write.csv(result, output_path, row.names = FALSE)
