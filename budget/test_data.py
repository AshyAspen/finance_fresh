#!/usr/bin/env python3
"""
Script to populate the database with realistic test data for ledger testing.
Run this to add sample income, bills, transactions, and balance points.
"""

from datetime import datetime, date, timedelta
import random

from .database import init_db, SessionLocal
from .models import Account, Transaction, Recurring, Balance, Goal, IrregularCategory, IrregularState


def create_test_data():
    """Create comprehensive test data for ledger functionality."""
    init_db()
    
    with SessionLocal() as session:
        # Clear existing data (optional - comment out if you want to keep existing data)
        session.query(Transaction).delete()
        session.query(Recurring).delete() 
        session.query(Balance).delete()
        session.query(Goal).delete()
        session.query(IrregularState).delete()  # Delete state first due to foreign key
        session.query(IrregularCategory).delete()
        
        # Ensure we have accounts
        checking = session.query(Account).filter_by(name="Default Checking").first()
        if not checking:
            checking = Account(name="Default Checking", type="checking")
            session.add(checking)
        
        savings = session.query(Account).filter_by(name="Savings").first()
        if not savings:
            savings = Account(name="Savings", type="savings")
            session.add(savings)
        
        session.commit()
        
        # Create a balance point from 2 weeks ago
        two_weeks_ago = datetime.now() - timedelta(days=14)
        initial_balance = Balance(
            amount=2500.00,
            timestamp=two_weeks_ago,
            account_id=checking.id
        )
        session.add(initial_balance)
        
        # Create recurring income (positive amounts)
        recurring_income = [
            Recurring(
                description="Salary - Tech Corp",
                amount=4200.00,
                start_date=datetime(2024, 1, 1),
                frequency="monthly",
                account_id=checking.id
            ),
            Recurring(
                description="Freelance Income",
                amount=800.00,
                start_date=datetime(2024, 1, 15),
                frequency="monthly", 
                account_id=checking.id
            ),
            Recurring(
                description="Interest Income",
                amount=15.50,
                start_date=datetime(2024, 1, 1),
                frequency="monthly",
                account_id=savings.id
            )
        ]
        
        # Create recurring bills (negative amounts)
        recurring_bills = [
            Recurring(
                description="Rent",
                amount=-1800.00,
                start_date=datetime(2024, 1, 1),
                frequency="monthly",
                account_id=checking.id
            ),
            Recurring(
                description="Electric Bill",
                amount=-120.00,
                start_date=datetime(2024, 1, 1),
                frequency="monthly",
                account_id=checking.id
            ),
            Recurring(
                description="Internet Service",
                amount=-85.00,
                start_date=datetime(2024, 1, 1),
                frequency="monthly",
                account_id=checking.id
            ),
            Recurring(
                description="Phone Bill",
                amount=-65.00,
                start_date=datetime(2024, 1, 1),
                frequency="monthly",
                account_id=checking.id
            ),
            Recurring(
                description="Car Insurance",
                amount=-180.00,
                start_date=datetime(2024, 1, 1),
                frequency="quarterly",
                account_id=checking.id
            ),
            Recurring(
                description="Gym Membership",
                amount=-45.00,
                start_date=datetime(2024, 1, 1),
                frequency="monthly",
                account_id=checking.id
            ),
            Recurring(
                description="Netflix",
                amount=-15.99,
                start_date=datetime(2024, 1, 1),
                frequency="monthly",
                account_id=checking.id
            ),
            Recurring(
                description="Spotify",
                amount=-9.99,
                start_date=datetime(2024, 1, 1),
                frequency="monthly",
                account_id=checking.id
            )
        ]
        
        # Add all recurring transactions
        for item in recurring_income + recurring_bills:
            session.add(item)
        
        # Create some one-time transactions from the past 30 days
        one_time_transactions = []
        
        # Generate random transactions over the past 30 days
        for i in range(25):  # 25 random transactions
            days_ago = random.randint(1, 30)
            transaction_date = datetime.now() - timedelta(days=days_ago)
            
            # Mix of expenses and occasional income
            if random.random() < 0.1:  # 10% chance of income
                descriptions = ["Freelance Bonus", "Gift", "Refund", "Side Project Payment"]
                amount = random.uniform(50, 500)
            else:  # 90% chance of expense
                descriptions = [
                    "Grocery Store", "Gas Station", "Restaurant - Lunch", "Coffee Shop",
                    "Amazon Purchase", "Target", "Uber Ride", "Movie Theater",
                    "Bookstore", "Hardware Store", "Pharmacy", "Doctor Visit",
                    "Car Maintenance", "Clothing Store", "Online Shopping"
                ]
                amount = -random.uniform(15, 200)
            
            description = random.choice(descriptions)
            
            transaction = Transaction(
                description=description,
                amount=round(amount, 2),
                timestamp=transaction_date,
                account_id=checking.id
            )
            one_time_transactions.append(transaction)
        
        # Add some specific transactions for testing edge cases
        specific_transactions = [
            Transaction(
                description="Large Purchase - Laptop",
                amount=-1200.00,
                timestamp=datetime.now() - timedelta(days=5),
                account_id=checking.id
            ),
            Transaction(
                description="Security Deposit Refund", 
                amount=800.00,
                timestamp=datetime.now() - timedelta(days=3),
                account_id=checking.id
            ),
            Transaction(
                description="Transfer to Savings",
                amount=-500.00,
                timestamp=datetime.now() - timedelta(days=1),
                account_id=checking.id
            ),
            Transaction(
                description="Transfer from Checking",
                amount=500.00,
                timestamp=datetime.now() - timedelta(days=1),
                account_id=savings.id
            )
        ]
        
        # Add all one-time transactions
        for transaction in one_time_transactions + specific_transactions:
            session.add(transaction)
        
        # Create some goals for testing
        goals = [
            Goal(
                description="Emergency Fund",
                amount=10000.00,
                target_date=datetime(2025, 12, 31),
                account_id=savings.id
            ),
            Goal(
                description="Vacation Fund", 
                amount=3000.00,
                target_date=datetime(2025, 6, 15),
                account_id=savings.id
            ),
            Goal(
                description="New Car Down Payment",
                amount=8000.00,
                target_date=datetime(2025, 8, 1),
                account_id=savings.id
            )
        ]
        
        for goal in goals:
            session.add(goal)
        
        # Create an irregular category for testing
        car_maintenance = IrregularCategory(
            name="Car Maintenance",
            account_id=checking.id,
            window_days=120,
            alpha=0.3,
            safety_quantile=0.8
        )
        session.add(car_maintenance)
        
        # Commit all data
        session.commit()
        
        print("✅ Test data created successfully!")
        print(f"   - Accounts: {checking.name}, {savings.name}")
        print(f"   - Recurring Income: {len(recurring_income)} items")
        print(f"   - Recurring Bills: {len(recurring_bills)} items")
        print(f"   - One-time Transactions: {len(one_time_transactions + specific_transactions)} items")
        print(f"   - Goals: {len(goals)} items")
        print(f"   - Initial Balance: ${initial_balance.amount:,.2f} on {initial_balance.timestamp.date()}")
        print("\nYou can now test the ledger functionality with realistic data!")


if __name__ == "__main__":
    create_test_data()