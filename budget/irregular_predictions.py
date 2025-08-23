"""
Irregular spending prediction engine.

Provides modular prediction methods for irregular categories:
- Deterministic: Statistical averages 
- Monte Carlo: Sampled distributions

Each category learns patterns separately per account.
Supports excluding periods when user wasn't tracking to prevent artificial gaps.
"""

import random
import json
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy.orm import Session
from .models import Transaction, IrregularCategory, IrregularState


class PredictionMethod(Enum):
    DETERMINISTIC = "deterministic"
    MONTE_CARLO = "monte_carlo"


@dataclass
class PredictedTransaction:
    """A predicted future irregular transaction."""
    date: date
    description: str
    amount: float
    account_id: int
    category_id: int
    confidence: float  # 0.0 to 1.0 - how confident we are in this prediction
    method: str        # "deterministic" or "monte_carlo"


@dataclass 
class CategoryAccountPattern:
    """Statistical pattern learned for a specific category in a specific account.
    
    For example, "Groceries" might have different patterns in:
    - Checking account: $150 every 7 days (weekly shopping)  
    - Credit card: $300 every 30 days (bulk purchases)
    """
    category_id: int
    account_id: int
    
    # === TIMING PATTERNS ===
    avg_gap_days: Optional[float] = None      # Average days between transactions (e.g., 7.2 days)
    weekday_probs: Optional[Dict[int, float]] = None  # Probability of transaction on each weekday
                                              # {0: 0.1, 6: 0.4} = 10% Monday, 40% Sunday
    last_event_date: Optional[date] = None    # Date of most recent transaction
    
    # === AMOUNT PATTERNS ===
    amount_mu: Optional[float] = None         # Mean amount (average spending per transaction)
    amount_sigma: Optional[float] = None      # Standard deviation (how much amounts vary)
    median_amount: Optional[float] = None     # Middle value when amounts sorted (less affected by outliers)
    
    # === TRACKING GAP HANDLING ===
    excluded_periods: List[Tuple[date, date]] = field(default_factory=list)  
    # Periods when user wasn't tracking transactions
    # Example: [(date(2024,1,1), date(2024,2,28))] = "wasn't tracking Jan-Feb"
    # TODO: Add UI for users to mark untracked periods to prevent artificial gaps
    #       from corrupting learned patterns (e.g., 7,7,7,100,7,7 -> 7,7,7,7,7)
    
    # === LEARNING METADATA ===
    transaction_count: int = 0                # How many historical transactions we learned from
    excluded_gap_count: int = 0               # How many gaps were excluded from learning
    confidence: float = 0.0                  # 0.0-1.0, higher with more data
    updated_at: Optional[datetime] = None     # When this pattern was last calculated


class PredictionStrategy(ABC):
    """Abstract base class for prediction strategies."""
    
    @abstractmethod
    def predict_transactions(
        self, 
        pattern: CategoryAccountPattern,
        start_date: date, 
        end_date: date,
        category_name: str
    ) -> List[PredictedTransaction]:
        """Generate predicted transactions for the given pattern and date range."""
        pass


class DeterministicPredictor(PredictionStrategy):
    """Deterministic prediction using statistical averages.
    
    Always predicts the same results for the same inputs:
    - Uses average gap between transactions for timing
    - Uses median amount for consistent spending 
    - Adjusts dates to preferred weekdays based on historical patterns
    """
    
    def predict_transactions(
        self,
        pattern: CategoryAccountPattern,
        start_date: date,
        end_date: date, 
        category_name: str
    ) -> List[PredictedTransaction]:
        """Generate predictions using statistical averages.
        
        Algorithm:
        1. Calculate when next occurrence should happen based on last event + average gap
        2. Create prediction every [average gap] days from that point  
        3. Use median amount for each prediction
        4. Adjust dates to preferred weekdays if pattern exists
        """
        predictions = []
        
        # VALIDATION: Need minimum data for reliable predictions
        # We need at least 2 transactions to calculate gaps
        if not pattern.avg_gap_days or pattern.transaction_count < 2:
            return predictions
            
        # STEP 1: Determine starting point for predictions
        if pattern.last_event_date:
            # Calculate when next occurrence SHOULD happen based on pattern
            next_expected = pattern.last_event_date + timedelta(days=int(pattern.avg_gap_days))
            # Don't predict in the past - start from requested date if expected date has passed
            current_date = max(next_expected, start_date)
        else:
            # No history, start from requested start date
            current_date = start_date
            
        # STEP 2: Generate predictions at regular intervals
        while current_date <= end_date:
            # STEP 2a: Calculate predicted amount
            # Priority: median > mean > fallback to 0
            # Median is preferred as it's less affected by outliers
            amount = pattern.median_amount or pattern.amount_mu or 0.0
            
            # STEP 2b: Adjust date to preferred weekday if we have weekday pattern data
            # Example: If groceries are usually bought on weekends, 
            # shift predicted Tuesday to nearby Saturday
            if pattern.weekday_probs:
                current_date = self._adjust_for_preferred_weekday(current_date, pattern.weekday_probs)
            
            # STEP 2c: Create prediction if still within date range
            if current_date <= end_date:
                predictions.append(PredictedTransaction(
                    date=current_date,
                    description=f"{category_name} (predicted)",
                    amount=amount,
                    account_id=pattern.account_id,
                    category_id=pattern.category_id,
                    confidence=pattern.confidence,
                    method="deterministic"
                ))
            
            # STEP 2d: Move to next predicted occurrence
            # Add average gap to get next prediction date
            current_date += timedelta(days=int(pattern.avg_gap_days))
            
        return predictions
    
    def _adjust_for_preferred_weekday(self, target_date: date, weekday_probs: Dict[int, float]) -> date:
        """Adjust date to most likely weekday within +/- 3 days.
        
        Example:
        - Target date: Tuesday (weekday 1)
        - Weekday probabilities: {5: 0.4, 6: 0.6} (40% Saturday, 60% Sunday)  
        - Will shift Tuesday to Sunday (highest probability within +/- 3 days)
        
        Args:
            target_date: The algorithmically calculated date
            weekday_probs: Dictionary of {weekday_index: probability}
                          where 0=Monday, 1=Tuesday, ..., 6=Sunday
        
        Returns:
            Adjusted date with higher probability weekday
        """
        if not weekday_probs:
            return target_date
            
        best_date = target_date
        best_prob = weekday_probs.get(target_date.weekday(), 0.0)
        
        # Check nearby days (within +/- 3 days) for better weekday probability
        # This prevents predictions from shifting too far from calculated timing
        for offset in [-3, -2, -1, 1, 2, 3]:
            candidate_date = target_date + timedelta(days=offset)
            candidate_prob = weekday_probs.get(candidate_date.weekday(), 0.0)
            if candidate_prob > best_prob:
                best_date = candidate_date
                best_prob = candidate_prob
                
        return best_date


class MonteCarloPredictor(PredictionStrategy):
    """Monte Carlo prediction using sampled distributions.
    
    Introduces realistic randomness:
    - Samples gap times from normal distribution around average
    - Samples amounts from learned distribution  
    - Samples weekdays based on historical probabilities
    
    Results will vary between runs, providing range of possible outcomes.
    """
    
    def predict_transactions(
        self,
        pattern: CategoryAccountPattern,
        start_date: date,
        end_date: date,
        category_name: str
    ) -> List[PredictedTransaction]:
        """Generate predictions using Monte Carlo sampling.
        
        Algorithm:
        1. Start from expected next occurrence or start_date
        2. Sample next gap time from normal distribution
        3. Sample amount from learned distribution
        4. Sample preferred weekday based on probabilities
        5. Repeat until end_date reached
        """
        predictions = []
        
        # VALIDATION: Need more data for stochastic modeling
        # Monte Carlo needs at least 3 points to estimate variation
        if not pattern.avg_gap_days or pattern.transaction_count < 3:
            return predictions
            
        # STEP 1: Initialize starting point
        if pattern.last_event_date:
            # Calculate when next occurrence SHOULD happen
            next_expected = pattern.last_event_date + timedelta(days=int(pattern.avg_gap_days))
            current_date = max(next_expected, start_date)
        else:
            current_date = start_date
            
        # STEP 2: Generate predictions with stochastic timing and amounts
        while current_date <= end_date:
            # STEP 2a: Sample next occurrence time using normal distribution
            # Standard deviation = 30% of mean (configurable parameter)
            # This creates realistic variation in timing
            gap_std = pattern.avg_gap_days * 0.3  # 30% variation around average
            gap_days = max(1, random.gauss(pattern.avg_gap_days, gap_std))
            current_date += timedelta(days=int(gap_days))
            
            if current_date > end_date:
                break
                
            # STEP 2b: Sample amount from learned distribution
            if pattern.amount_sigma and pattern.amount_mu:
                # Use learned normal distribution (mean and std dev)
                # Ensures amount is never negative
                amount = max(0, random.gauss(pattern.amount_mu, pattern.amount_sigma))
            elif pattern.median_amount:
                # Fallback: use median with 20% variation 
                # Creates reasonable spread when we don't have std dev
                variation = pattern.median_amount * 0.2
                amount = max(0, random.gauss(pattern.median_amount, variation))
            else:
                # Last resort: use mean without variation
                amount = pattern.amount_mu or 0.0
            
            # STEP 2c: Sample weekday preference based on historical probabilities
            if pattern.weekday_probs:
                current_date = self._sample_preferred_weekday(current_date, pattern.weekday_probs)
            
            # STEP 2d: Create prediction with lower confidence (stochastic uncertainty)
            predictions.append(PredictedTransaction(
                date=current_date,
                description=f"{category_name} (predicted)",
                amount=amount,
                account_id=pattern.account_id,
                category_id=pattern.category_id,
                confidence=pattern.confidence * 0.8,  # 20% confidence reduction for randomness
                method="monte_carlo"
            ))
            
        return predictions
    
    def _sample_preferred_weekday(self, target_date: date, weekday_probs: Dict[int, float]) -> date:
        """Sample a weekday based on probability distribution within +/- 3 days.
        
        Uses weighted random sampling to choose weekday based on historical patterns.
        
        Example:
        - Target: Wednesday
        - Probabilities: Monday=0.1, Tuesday=0.1, Saturday=0.4, Sunday=0.4
        - Might randomly select Saturday or Sunday based on their higher probabilities
        
        Args:
            target_date: The base date to adjust
            weekday_probs: Historical weekday probabilities {weekday: probability}
            
        Returns:
            Date within +/- 3 days, sampled based on weekday probabilities
        """
        if not weekday_probs:
            return target_date
            
        # STEP 1: Build list of candidate dates and their probabilities
        candidates = []
        weights = []
        
        for offset in range(-3, 4):  # Check days from -3 to +3
            candidate_date = target_date + timedelta(days=offset)
            # Get probability for this weekday, default to small value if not in pattern
            prob = weekday_probs.get(candidate_date.weekday(), 0.1)
            candidates.append(candidate_date)
            weights.append(prob)
            
        # STEP 2: Use weighted random choice to select date
        # Higher probability weekdays are more likely to be chosen
        return random.choices(candidates, weights=weights)[0]


class IrregularPredictionEngine:
    """Main prediction engine for irregular spending categories.
    
    Orchestrates the learning and prediction process:
    1. Learns patterns from historical transactions per category per account
    2. Applies chosen prediction strategy (deterministic vs Monte Carlo)  
    3. Generates predictions for ledger integration
    4. Handles excluded periods to prevent artificial gaps from corrupting patterns
    """
    
    def __init__(self, method: PredictionMethod = PredictionMethod.DETERMINISTIC):
        self.method = method
        self.strategies = {
            PredictionMethod.DETERMINISTIC: DeterministicPredictor(),
            PredictionMethod.MONTE_CARLO: MonteCarloPredictor()
        }
    
    def set_method(self, method: PredictionMethod) -> None:
        """Change prediction method dynamically."""
        self.method = method
    
    def learn_patterns(self, session: Session) -> Dict[Tuple[int, int], CategoryAccountPattern]:
        """Learn patterns from historical transactions for all category-account combinations.
        
        Returns a dictionary keyed by (category_id, account_id) tuples.
        
        Example result:
        {
            (1, 1): CategoryAccountPattern(groceries in checking),
            (1, 2): CategoryAccountPattern(groceries in credit card),  
            (2, 1): CategoryAccountPattern(gas in checking),
        }
        """
        patterns = {}
        
        # Get all active irregular categories
        categories = session.query(IrregularCategory).filter(IrregularCategory.active == True).all()
        
        for category in categories:
            # Get all transactions tagged to this category
            transactions = session.query(Transaction).filter(
                Transaction.category_id == category.id
            ).order_by(Transaction.timestamp).all()
            
            if not transactions:
                continue
                
            # STEP 1: Group transactions by account
            # Same category might be used in different accounts with different patterns
            account_transactions = {}
            for tx in transactions:
                if tx.account_id not in account_transactions:
                    account_transactions[tx.account_id] = []
                account_transactions[tx.account_id].append(tx)
            
            # STEP 2: Learn separate pattern for each account
            for account_id, tx_list in account_transactions.items():
                pattern = self._learn_account_pattern(category.id, account_id, tx_list)
                patterns[(category.id, account_id)] = pattern
                
        return patterns
    
    def _learn_account_pattern(self, category_id: int, account_id: int, transactions: List[Transaction]) -> CategoryAccountPattern:
        """Learn statistical pattern for a category in a specific account.
        
        Analyzes historical transactions to extract:
        - Average time gaps between transactions (excluding artificial gaps)
        - Weekday preferences (which days are transactions more likely)
        - Amount statistics (mean, std dev, median)
        - Confidence level based on data quantity
        
        Args:
            category_id: ID of irregular category (e.g., groceries)
            account_id: ID of account (e.g., checking vs credit card)
            transactions: List of historical transactions for this category+account combo
            
        Returns:
            CategoryAccountPattern with learned statistics
        """
        # VALIDATION: Need at least 2 transactions to calculate gaps
        if len(transactions) < 2:
            return CategoryAccountPattern(
                category_id=category_id, 
                account_id=account_id, 
                transaction_count=len(transactions)
            )
        
        # STEP 1: Sort transactions chronologically and extract data
        transactions.sort(key=lambda t: t.timestamp)
        
        # Initialize data collection lists
        all_gaps = []           # All gaps (including artificial ones)
        valid_gaps = []         # Gaps excluding artificial tracking breaks
        weekday_counts = [0] * 7  # Count of transactions per weekday (Mon=0, Sun=6)
        amounts = []            # Transaction amounts (absolute values for spending)
        
        # TODO: Load excluded periods from database or user settings
        # For now, use empty list - will be populated when UI is implemented
        excluded_periods = []   # List of (start_date, end_date) tuples
        
        excluded_gap_count = 0  # Track how many gaps were excluded
        
        for i, tx in enumerate(transactions):
            # Convert timestamp to date for calculations
            tx_date = tx.timestamp.date() if isinstance(tx.timestamp, datetime) else tx.timestamp
            
            # Record amount (use absolute value since we're modeling spending patterns)
            amounts.append(abs(tx.amount))
            
            # Count weekday occurrence
            weekday_counts[tx_date.weekday()] += 1
            
            # Calculate gap from previous transaction (skip first transaction)
            if i > 0:
                prev_tx = transactions[i-1]
                prev_date = prev_tx.timestamp.date() if isinstance(prev_tx.timestamp, datetime) else prev_tx.timestamp
                gap_days = (tx_date - prev_date).days
                all_gaps.append(gap_days)
                
                # Check if this gap should be excluded due to tracking breaks
                gap_overlaps_excluded = self._gap_overlaps_excluded_period(
                    prev_date, tx_date, excluded_periods
                )
                
                if gap_overlaps_excluded:
                    excluded_gap_count += 1
                    # TODO: When UI is implemented, show user which gaps were excluded
                    #       Example: "Gap of 90 days excluded (marked as untracked period)"
                else:
                    valid_gaps.append(gap_days)
        
        # STEP 2: Calculate timing statistics using only valid gaps
        # This prevents artificial gaps from corrupting the average
        # Example: [7,7,7,100,7,7] -> [7,7,7,7,7] if 100-day gap was during untracked period
        gaps_for_calculation = valid_gaps if valid_gaps else all_gaps
        avg_gap_days = sum(gaps_for_calculation) / len(gaps_for_calculation) if gaps_for_calculation else None
        
        # Weekday probabilities = frequency of each weekday
        # Example: [2, 1, 0, 0, 0, 3, 1] -> {0: 0.29, 1: 0.14, 5: 0.43, 6: 0.14}
        total_tx = len(transactions)
        weekday_probs = {i: count/total_tx for i, count in enumerate(weekday_counts) if count > 0}
        
        # STEP 3: Calculate amount statistics
        amount_mu = sum(amounts) / len(amounts) if amounts else 0  # Mean
        
        # Standard deviation (measure of amount variability)
        amount_sigma = None
        if len(amounts) > 1:
            # Sample standard deviation formula: sqrt(sum((x-mean)²) / (n-1))
            variance = sum((a - amount_mu) ** 2 for a in amounts) / (len(amounts) - 1)
            amount_sigma = variance ** 0.5
        
        # Median (middle value, less affected by outliers than mean)
        amounts.sort()
        median_amount = amounts[len(amounts) // 2] if amounts else 0
        
        # STEP 4: Calculate confidence based on data quantity and gap exclusions
        # More transactions = higher confidence, max out at 10+ transactions
        # Reduce confidence if many gaps were excluded (less reliable pattern)
        base_confidence = min(1.0, len(transactions) / 10.0)
        exclusion_penalty = min(0.3, excluded_gap_count * 0.1)  # Up to 30% penalty
        confidence = max(0.1, base_confidence - exclusion_penalty)
        
        # STEP 5: Record most recent transaction date for future predictions
        last_tx = transactions[-1]
        last_event_date = last_tx.timestamp.date() if isinstance(last_tx.timestamp, datetime) else last_tx.timestamp
        
        return CategoryAccountPattern(
            category_id=category_id,
            account_id=account_id,
            avg_gap_days=avg_gap_days,
            weekday_probs=weekday_probs,
            last_event_date=last_event_date,
            amount_mu=amount_mu,
            amount_sigma=amount_sigma,
            median_amount=median_amount,
            excluded_periods=excluded_periods,
            transaction_count=len(transactions),
            excluded_gap_count=excluded_gap_count,
            confidence=confidence,
            updated_at=datetime.utcnow()
        )
    
    def _gap_overlaps_excluded_period(self, gap_start: date, gap_end: date, excluded_periods: List[Tuple[date, date]]) -> bool:
        """Check if a transaction gap overlaps with any excluded tracking period.
        
        A gap "overlaps" if any part of the time between transactions falls within
        an excluded period, indicating the gap might be artificially inflated.
        
        Args:
            gap_start: Date of previous transaction
            gap_end: Date of current transaction  
            excluded_periods: List of (start, end) date tuples for untracked periods
            
        Returns:
            True if gap should be excluded from pattern learning
            
        Example:
            gap_start = Jan 1, gap_end = Mar 1 (60-day gap)
            excluded_periods = [(Jan 15, Feb 15)]  # User wasn't tracking for a month
            Returns: True (gap overlaps excluded period, should not count toward pattern)
        """
        for exclude_start, exclude_end in excluded_periods:
            # Check if there's any overlap between gap period and excluded period
            # Gap period: [gap_start, gap_end]
            # Excluded period: [exclude_start, exclude_end]
            # Overlap exists if: gap_start <= exclude_end AND gap_end >= exclude_start
            
            if gap_start <= exclude_end and gap_end >= exclude_start:
                return True  # Gap overlaps excluded period
                
        return False  # Gap is clean, can be used for learning
    
    def predict_for_account(
        self, 
        session: Session,
        account_id: int, 
        start_date: date, 
        end_date: date,
        patterns: Optional[Dict[Tuple[int, int], CategoryAccountPattern]] = None
    ) -> List[PredictedTransaction]:
        """Generate predictions for all irregular categories in a specific account.
        
        This is the main method called by the ledger to get irregular predictions.
        
        Args:
            session: Database session
            account_id: Which account to predict for  
            start_date: Start of prediction period
            end_date: End of prediction period
            patterns: Pre-computed patterns (optional, will learn if not provided)
            
        Returns:
            List of predicted transactions sorted by date
        """
        # Learn patterns if not provided (allows caching for performance)
        if patterns is None:
            patterns = self.learn_patterns(session)
        
        predictions = []
        strategy = self.strategies[self.method]
        
        # Get category names for readable descriptions
        categories = {cat.id: cat.name for cat in session.query(IrregularCategory).filter(IrregularCategory.active == True).all()}
        
        # STEP 1: Find all patterns that apply to this account
        for (category_id, pat_account_id), pattern in patterns.items():
            if pat_account_id == account_id and pattern.transaction_count >= 2:
                category_name = categories.get(category_id, "Unknown Category")
                
                # STEP 2: Generate predictions using chosen strategy
                account_predictions = strategy.predict_transactions(
                    pattern, start_date, end_date, category_name
                )
                predictions.extend(account_predictions)
        
        # STEP 3: Sort predictions chronologically for ledger integration
        predictions.sort(key=lambda p: p.date)
        return predictions


# === CONVENIENCE FUNCTIONS FOR LEDGER INTEGRATION ===

def get_prediction_engine(method: str = "deterministic") -> IrregularPredictionEngine:
    """Get prediction engine instance with specified method.
    
    Args:
        method: "deterministic" or "monte_carlo"
        
    Returns:
        Configured prediction engine
    """
    pred_method = PredictionMethod.DETERMINISTIC if method == "deterministic" else PredictionMethod.MONTE_CARLO
    return IrregularPredictionEngine(pred_method)


def predict_irregular_transactions(
    session: Session,
    account_id: int,
    start_date: date,
    end_date: date,
    method: str = "deterministic"
) -> List[PredictedTransaction]:
    """Convenience function to get irregular predictions for an account.
    
    This is the main entry point for ledger integration.
    
    Args:
        session: Database session
        account_id: Account to predict for
        start_date: Start of prediction window  
        end_date: End of prediction window
        method: "deterministic" or "monte_carlo"
        
    Returns:
        List of predicted transactions for the account
    """
    engine = get_prediction_engine(method)
    return engine.predict_for_account(session, account_id, start_date, end_date)