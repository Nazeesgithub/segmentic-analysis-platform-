"""
SegmentIQ: Customer Dataset Generator
Generates 8,950 credit card customers with clusterable features
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

# Set random seed for reproducibility
np.random.seed(42)

# Define dataset size
n_customers = 8950

# Generate customer data
data = {
    # 1. Age (18-80 years old)
    'Age': np.random.normal(loc=45, scale=15, size=n_customers).astype(int),
    
    # 2. Income (Annual income in USD: 20k-200k)
    'Annual_Income': np.random.normal(loc=75000, scale=40000, size=n_customers).astype(int),
    
    # 3. Spending_Score (0-100: How much they spend)
    'Spending_Score': np.random.randint(0, 101, n_customers),
    
    # 4. Credit_Limit (USD: 5k-50k)
    'Credit_Limit': np.random.normal(loc=20000, scale=8000, size=n_customers).astype(int),
    
    # 5. Purchase_Frequency (times per month)
    'Purchase_Frequency': np.random.poisson(lam=5, size=n_customers),
    
    # 6. Customer_Tenure (years as customer: 0-20)
    'Customer_Tenure': np.random.randint(0, 21, n_customers),
    
    # 7. Average_Transaction_Value (USD: 50-500)
    'Avg_Transaction_Value': np.random.uniform(50, 500, n_customers).round(2),
    
    # 8. Product_Category (Categorical: Home, Electronics, Fashion, Food, Travel)
    'Product_Category': np.random.choice(
        ['Home', 'Electronics', 'Fashion', 'Food', 'Travel'], 
        n_customers
    ),
    
    # 9. Payment_Status (Categorical: On-Time, Delayed, Late)
    'Payment_Status': np.random.choice(
        ['On-Time', 'Delayed', 'Late'], 
        n_customers,
        p=[0.7, 0.2, 0.1]
    ),
    
    # 10. Credit_Utilization_Ratio (0-1: Used credit / Total limit)
    'Credit_Utilization_Ratio': np.random.uniform(0, 1, n_customers).round(2),
    
    # 11. Days_Since_Last_Transaction (days: 0-180)
    'Days_Since_Last_Transaction': np.random.randint(0, 181, n_customers),
    
    # 12. Account_Type (Categorical: Standard, Gold, Platinum)
    'Account_Type': np.random.choice(
        ['Standard', 'Gold', 'Platinum'], 
        n_customers,
        p=[0.5, 0.35, 0.15]
    ),
}

# Ensure Age is within valid range
data['Age'] = np.clip(data['Age'], 18, 80)

# Ensure Income is positive
data['Annual_Income'] = np.clip(data['Annual_Income'], 20000, 200000)

# Ensure Credit_Limit is positive and correlates with income
data['Credit_Limit'] = np.clip(
    (data['Credit_Limit'] + (data['Annual_Income'] / 5000)).astype(int),
    5000, 50000
)

# Create DataFrame
df = pd.DataFrame(data)

# Add a unique customer ID
df.insert(0, 'Customer_ID', range(1, n_customers + 1))

print("=" * 80)
print("SegmentIQ: Customer Dataset Generated Successfully!")
print("=" * 80)
print(f"\n📊 Dataset Shape: {df.shape}")
print(f"   Rows (Customers): {df.shape[0]}")
print(f"   Columns (Features): {df.shape[1]}")

print("\n📋 Feature Overview:")
print(df.head(10))

print("\n📈 Statistical Summary (Numerical Features):")
print(df.describe().round(2))

print("\n🔍 Data Types:")
print(df.dtypes)

print("\n🎯 Categorical Feature Value Counts:")
for col in ['Product_Category', 'Payment_Status', 'Account_Type']:
    print(f"\n{col}:")
    print(df[col].value_counts())

print("\n✅ Missing Values Check:")
print(df.isnull().sum())

# Check for correlations (for PCA analysis)
print("\n🔗 Correlation Matrix (Numerical Features Only):")
numerical_cols = df.select_dtypes(include=[np.number]).columns.tolist()
correlation_matrix = df[numerical_cols].corr()
print(correlation_matrix.round(2))

# Save dataset
output_path = Path(__file__).parent / 'data' / 'customers.csv'
output_path.parent.mkdir(exist_ok=True)
df.to_csv(output_path, index=False)
print(f"\n💾 Dataset saved to: {output_path}")

# Verify file was saved
if output_path.exists():
    print(f"✅ File size: {output_path.stat().st_size / 1024:.2f} KB")
    print(f"✅ Records: {len(pd.read_csv(output_path))}")
else:
    print("❌ Error: Could not save file!")

print("\n" + "=" * 80)
print("NEXT STEP: Run the Jupyter Notebook '02_ml_pipeline.ipynb'")
print("=" * 80)
