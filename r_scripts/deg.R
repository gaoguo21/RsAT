args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) {
  stop("Usage: run_de.R <counts_csv> <meta_json> <out_csv>")
}

counts_path <- args[1]
meta_path <- args[2]
out_path <- args[3]

library(jsonlite)

meta <- fromJSON(meta_path)
group_map <- meta$group_map
method <- tolower(meta$method)
min_count <- suppressWarnings(as.numeric(meta$min_count))
if (is.na(min_count)) {
  min_count <- 2
}
if (min_count < 0) {
  min_count <- 0
}

counts <- read.csv(counts_path, check.names = FALSE, stringsAsFactors = FALSE)
gene_col <- colnames(counts)[1]
genes <- counts[[gene_col]]
counts <- counts[, -1, drop = FALSE]

# Merge duplicate gene names by averaging their counts per sample.
if (any(duplicated(genes))) {
  counts$.__gene__ <- genes
  counts <- aggregate(. ~ .__gene__, data = counts, FUN = mean, na.rm = TRUE)
  genes <- counts$.__gene__
  counts <- counts[, -1, drop = FALSE]
}

sample_names <- colnames(counts)
groups <- sapply(sample_names, function(x) {
  if (!is.null(group_map[[x]])) group_map[[x]] else "ignore"
})

keep <- groups %in% c("A", "B")
counts <- counts[, keep, drop = FALSE]
groups <- groups[keep]

if (!all(c("A", "B") %in% groups)) {
  stop("Both Group A and Group B must have at least one sample.")
}

rownames(counts) <- genes
counts <- as.matrix(counts)
storage.mode(counts) <- "integer"

total_counts <- rowSums(counts, na.rm = TRUE)
counts <- counts[total_counts >= min_count, , drop = FALSE]

if (nrow(counts) == 0) {
  stop(paste0("No genes left after filtering (total count < ", min_count, ")."))
}

if (method == "edger") {
  suppressPackageStartupMessages(library(edgeR))

  group_factor <- factor(groups)
  dge <- DGEList(counts = counts, group = group_factor)
  dge <- calcNormFactors(dge)
  dge <- estimateDisp(dge)
  et <- exactTest(dge)
  res <- topTags(et, n = Inf)$table
  res <- data.frame(
    gene = rownames(res),
    log2FC = res$logFC,
    pvalue = res$PValue,
    FDR = res$FDR,
    stringsAsFactors = FALSE,
    check.names = FALSE
  )
} else if (method == "deseq2") {
  suppressPackageStartupMessages(library(DESeq2))

  col_data <- data.frame(
    row.names = colnames(counts),
    group = factor(groups)
  )

  dds <- DESeqDataSetFromMatrix(countData = counts, colData = col_data, design = ~ group)
  dds <- DESeq(dds)
  res <- results(dds, contrast = c("group", "B", "A"))
  res <- as.data.frame(res)
  res$gene <- rownames(res)
  res <- res[, c("gene", "log2FoldChange", "pvalue", "padj")]
  colnames(res) <- c("gene", "log2FC", "pvalue", "p-adj")
} else {
  stop("Unsupported method.")
}

write.csv(res, out_path, row.names = FALSE)
