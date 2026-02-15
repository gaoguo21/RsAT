args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 3) {
  stop("Usage: Rscript id2symbol.R <input_path> <output_path> <organism>")
}

input_path <- args[1]
output_path <- args[2]
organism <- tolower(args[3])

if (!requireNamespace("AnnotationDbi", quietly = TRUE)) {
  stop("AnnotationDbi package is required.")
}

db <- NULL
if (organism == "human") {
  if (!requireNamespace("org.Hs.eg.db", quietly = TRUE)) {
    stop("org.Hs.eg.db package is required.")
  }
  db <- org.Hs.eg.db::org.Hs.eg.db
} else if (organism == "mouse") {
  if (!requireNamespace("org.Mm.eg.db", quietly = TRUE)) {
    stop("org.Mm.eg.db package is required.")
  }
  db <- org.Mm.eg.db::org.Mm.eg.db
} else {
  stop("Organism must be human or mouse.")
}

read_table <- function(path) {
  if (grepl("\\.xlsx?$", path, ignore.case = TRUE)) {
    if (!requireNamespace("readxl", quietly = TRUE)) {
      stop("readxl package is required for Excel input.")
    }
    return(readxl::read_excel(path))
  } else if (grepl("\\.csv$", path, ignore.case = TRUE)) {
    return(tryCatch(read.csv(path, stringsAsFactors = FALSE),
                    error = function(e) read.table(path, header = TRUE, sep = ",", stringsAsFactors = FALSE)))
  }
  return(read.table(path, header = TRUE, stringsAsFactors = FALSE))
}

df <- read_table(input_path)
if (ncol(df) < 1) {
  stop("Input file must have at least one column of gene IDs.")
}

ids <- as.character(df[[1]])
ids <- ids[!is.na(ids)]

detect_keytype <- function(ids) {
  if (any(grepl("^ENSG", ids, ignore.case = TRUE)) || any(grepl("^ENSMUSG", ids, ignore.case = TRUE))) {
    return("ENSEMBL")
  }
  if (all(grepl("^[0-9]+$", ids))) {
    return("ENTREZID")
  }
  return("ENSEMBL")
}

keytype <- detect_keytype(ids)

mapped <- AnnotationDbi::select(
  db,
  keys = unique(ids),
  keytype = keytype,
  columns = c("SYMBOL")
)

mapped <- mapped[!duplicated(mapped[[keytype]]), ]
colnames(mapped)[colnames(mapped) == keytype] <- "input_id"
colnames(mapped)[colnames(mapped) == "SYMBOL"] <- "symbol"

df$input_id <- as.character(df[[1]])
symbol_map <- mapped$symbol[match(df$input_id, mapped$input_id)]
out <- cbind(df, symbol = symbol_map)

write.csv(out, output_path, row.names = FALSE)
