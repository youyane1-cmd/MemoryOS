import json
import re
from typing import List, Dict
from collections import defaultdict
import statistics
from openpyxl import Workbook

def simple_tokenize(text: str) -> List[str]:
    """Simple tokenization function."""
    if not text:
        return []
    
    # Convert to string if not already
    text = str(text).lower()
    # Remove punctuation and split by whitespace using regex (正确的方法)
    tokens = re.findall(r'\b\w+\b', text)
    return tokens

def calculate_metrics(prediction: str, reference: str) -> Dict[str, float]:
    """Calculate precision, recall, and F1 for prediction against reference."""
    # Tokenize both prediction and reference
    pred_tokens = set(simple_tokenize(prediction))
    ref_tokens = set(simple_tokenize(reference))
    
    # Calculate intersection
    common_tokens = pred_tokens & ref_tokens
    
    # Calculate precision and recall
    precision = len(common_tokens) / len(pred_tokens) if len(pred_tokens) > 0 else 0
    recall = len(common_tokens) / len(ref_tokens) if len(ref_tokens) > 0 else 0
    
    # Calculate F1 score
    if precision + recall > 0:
        f1 = 2 * (precision * recall) / (precision + recall)
    else:
        f1 = 0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }

def load_data(file_path: str) -> List[Dict]:
    """Load data from a JSON file."""
    with open(file_path, 'r', encoding='utf-8') as file:
        data = json.load(file)
    return data

def write_metrics_excel(total_metrics: Dict[str, float], category_metrics: List[Dict], output_file: str):
    """Write total and per-category metrics to an Excel file."""
    wb = Workbook()

    total_sheet = wb.active
    total_sheet.title = "Total"
    total_sheet.append(["sample_count", "precision", "recall", "f1"])
    total_sheet.append([
        total_metrics["sample_count"],
        total_metrics["precision"],
        total_metrics["recall"],
        total_metrics["f1"],
    ])

    category_sheet = wb.create_sheet("By Category")
    category_sheet.append(["category", "sample_count", "precision", "recall", "f1"])
    for item in category_metrics:
        category_sheet.append([
            item["category"],
            item["sample_count"],
            item["precision"],
            item["recall"],
            item["f1"],
        ])

    wb.save(output_file)

def main(file_path: str):
    """Main function to calculate average metrics and export Excel."""
    # Load data from file
    data = load_data(file_path)
    
    # Initialize category dictionary
    category_metrics = defaultdict(lambda: {
        "precision": [],
        "recall": [],
        "f1": [],
    })
    all_metrics = {
        "precision": [],
        "recall": [],
        "f1": [],
    }
    
    # Calculate metrics for each sample
    for sample in data:
        category = sample['category']
        system_answer = sample['system_answer']
        original_answer = sample['original_answer']
        
        metrics = calculate_metrics(system_answer, original_answer)
        
        for key in ["precision", "recall", "f1"]:
            category_metrics[category][key].append(metrics[key])
            all_metrics[key].append(metrics[key])
    
    total_metrics = {
        "sample_count": len(data),
        "precision": statistics.mean(all_metrics["precision"]) if all_metrics["precision"] else 0,
        "recall": statistics.mean(all_metrics["recall"]) if all_metrics["recall"] else 0,
        "f1": statistics.mean(all_metrics["f1"]) if all_metrics["f1"] else 0,
    }

    category_rows = []
    for category, metrics in sorted(category_metrics.items()):
        row = {
            "category": category,
            "sample_count": len(metrics["f1"]),
            "precision": statistics.mean(metrics["precision"]) if metrics["precision"] else 0,
            "recall": statistics.mean(metrics["recall"]) if metrics["recall"] else 0,
            "f1": statistics.mean(metrics["f1"]) if metrics["f1"] else 0,
        }
        category_rows.append(row)
        print(
            f"Category {category}: "
            f"Precision = {row['precision']:.4f}, "
            f"Recall = {row['recall']:.4f}, "
            f"F1 = {row['f1']:.4f}"
        )

    output_file = "mem_tmp_loco_final/evaluation_metrics.xlsx"
    write_metrics_excel(total_metrics, category_rows, output_file)
    print(
        f"Total: Precision = {total_metrics['precision']:.4f}, "
        f"Recall = {total_metrics['recall']:.4f}, "
        f"F1 = {total_metrics['f1']:.4f}"
    )
    print(f"Excel result saved to {output_file}")

if __name__ == "__main__":
    file_path = "mem_tmp_loco_final/all_loco_results.json"  # 使用main_loco_parse.py生成的文件
    main(file_path)