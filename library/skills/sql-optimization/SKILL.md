---
name: sql-optimization
description: SQL Server query optimization (performance tuning, indexing strategies, window functions, execution plans, batch operations)
user-invokable: true
---

# SQL Server Query Optimization

Guide for optimizing SQL Server queries. Covers query performance analysis, indexing strategies, execution plans, pagination optimization, batch operations, and monitoring.

## Core Optimization Principles

### Avoid SELECT * (Anti-Pattern)

```sql
-- ❌ BAD: Retrieving unnecessary columns
SELECT * FROM dbo.Customers 
WHERE StoreId = 1

-- ✅ GOOD: Explicit column selection
SELECT 
    Id, 
    CustomerNumber, 
    Surname, 
    Forename, 
    StoreId,
    CustomerStatus
FROM dbo.Customers
WHERE StoreId = 1
```

### Join Optimization

```sql
-- ❌ BAD: Inefficient join order, unnecessary LEFT JOINs
SELECT 
    c.Surname,
    o.FiscalYear,
    d.Amount
FROM dbo.Customers c
LEFT JOIN dbo.Orders o ON c.Id = o.CustomerId
LEFT JOIN dbo.Discounts d ON o.Id = d.OrderId
WHERE o.Status = 1  -- Filter after join

-- ✅ GOOD: Inner joins, filter early
SELECT 
    c.Surname,
    o.FiscalYear,
    d.Amount
FROM dbo.Customers c
INNER JOIN dbo.Orders o ON c.Id = o.CustomerId
    AND o.Status = 1  -- Filter in join condition
INNER JOIN dbo.Discounts d ON o.Id = d.OrderId
WHERE c.StoreId = 1
```

### Filter Early in WHERE Clause

```sql
-- ❌ BAD: Complex operations in WHERE prevent index usage
SELECT c.Surname, c.Forename
FROM dbo.Customers c
WHERE UPPER(c.Surname) = 'MORI'

-- ✅ GOOD: Direct column reference (case-insensitive collation)
SELECT c.Surname, c.Forename
FROM dbo.Customers c
WHERE c.Surname = 'Smith'  -- case-insensitive collation handles case
```

## Indexing Strategy

### Index Design for Customer Queries

```sql
-- ❌ BAD: Single large composite index
CREATE INDEX idx_customer_all 
ON dbo.Customers(
    StoreId, 
    CustomerStatus, 
    Surname, 
    Forename, 
    CreatedDate
);

-- ✅ GOOD: Focused indexes by query pattern

-- For store customer orders (most common)
CREATE INDEX idx_customers_store_status
ON dbo.Customers(StoreId, CustomerStatus)
INCLUDE (CustomerNumber, Surname, Forename);

-- For signup year filtering
CREATE INDEX idx_customers_signup_year
ON dbo.Customers(SignupYear, CustomerStatus)
INCLUDE (Surname, Forename, StoreId);

-- For name searches
CREATE INDEX idx_customers_surname_forename
ON dbo.Customers(Surname, Forename)
INCLUDE (StoreId, SignupYear);
```

### Covering Indexes (Include Non-Clustered Columns)

```sql
-- ✅ GOOD: Covering index for full query execution without table lookup
CREATE INDEX idx_orders_fiscal_year
ON dbo.Orders(CustomerId, FiscalYear)
INCLUDE (Status, Rating, OrderDate);

-- Query can now use index leaf pages only (no RID lookup)
SELECT CustomerId, FiscalYear, Status, Rating, OrderDate
FROM dbo.Orders
WHERE CustomerId = 12345 AND FiscalYear = '2024'
```

### Filtered/Partial Indexes

```sql
-- ✅ GOOD: Index only active customers (smaller, faster)
CREATE INDEX idx_active_customers
ON dbo.Customers(StoreId, Surname)
WHERE CustomerStatus IN (1, 3, 5);  -- Active statuses

-- Index supports most queries without including inactive customers
SELECT Surname, Forename
FROM dbo.Customers
WHERE StoreId = 1 AND CustomerStatus = 3
```

## Execution Plan Analysis

### Enable Query Plan Analysis

```sql
-- SQL Server: View execution plan
SET STATISTICS IO ON;
SET STATISTICS TIME ON;
GO

SELECT c.Surname, o.FiscalYear, o.Rating
FROM dbo.Customers c
INNER JOIN dbo.Orders o ON c.Id = o.CustomerId
WHERE c.StoreId = 1;

GO
SET STATISTICS IO OFF;
SET STATISTICS TIME OFF;
```

### Red Flags in Execution Plans

- **Full Table Scan**: Index not being used (check column type compatibility)
- **Nested Loops**: OK for small result sets; consider when joining large tables
- **Sort**: May indicate missing index or wrong join order
- **Hash Match**: Usually for large joins; monitor memory usage
- **RID/Clustered Index Seek after Non-Clustered**: Missing INCLUDE columns

## Pagination Optimization

### Anti-Pattern: OFFSET/FETCH (Slow for Large Offsets)

```sql
-- ❌ BAD: Slow for page 1000+ due to skipping rows
SELECT c.Surname, c.Forename
FROM dbo.Customers c
WHERE c.StoreId = 1
ORDER BY c.Surname, c.Id
OFFSET 10000 ROWS FETCH NEXT 50 ROWS ONLY;

-- ✅ GOOD: Cursor-based pagination using last known ID
DECLARE @LastId INT = 0;  -- From previous page

SELECT TOP 50
    c.Id,
    c.Surname,
    c.Forename
FROM dbo.Customers c
WHERE c.StoreId = 1
    AND c.Id > @LastId  -- Continue from last record
ORDER BY c.Id;

-- ✅ GOOD: Cursor-based using timestamp
DECLARE @LastDate DATETIME2 = '2024-01-01T00:00:00';

SELECT TOP 50
    c.Id,
    c.Surname,
    c.CreatedDate
FROM dbo.Customers c
WHERE c.CreatedDate > @LastDate
    AND c.StoreId = 1
ORDER BY c.CreatedDate, c.Id;
```

## Window Functions and Aggregations

### Avoid Multiple Aggregation Queries

```sql
-- ❌ BAD: Multiple separate queries for order statistics
SELECT COUNT(*) FROM dbo.Orders WHERE Status = 20;
SELECT COUNT(*) FROM dbo.Orders WHERE Status = 21;
SELECT COUNT(*) FROM dbo.Orders WHERE Status = 22;

-- ✅ GOOD: Single query with conditional aggregation
SELECT 
    COUNT(CASE WHEN Status = 20 THEN 1 END) as placed,
    COUNT(CASE WHEN Status = 21 THEN 1 END) as shipped,
    COUNT(CASE WHEN Status = 22 THEN 1 END) as cancelled,
    COUNT(*) as total
FROM dbo.Orders
WHERE FiscalYear = '2024';
```

### Use Window Functions Instead of Correlated Subqueries

```sql
-- ❌ BAD: Correlated subquery (N+1 pattern)
SELECT 
    o.Id,
    o.CustomerId,
    o.Rating,
    (SELECT AVG(Rating) FROM dbo.Orders o2 WHERE o2.CustomerId = o.CustomerId) as customer_avg_rating
FROM dbo.Orders o
WHERE o.FiscalYear = '2024';

-- ✅ GOOD: Window function (single pass)
SELECT 
    o.Id,
    o.CustomerId,
    o.Rating,
    AVG(o.Rating) OVER (PARTITION BY o.CustomerId) as customer_avg_rating,
    ROW_NUMBER() OVER (PARTITION BY o.CustomerId ORDER BY o.Rating DESC) as rating_rank
FROM dbo.Orders o
WHERE o.FiscalYear = '2024';
```

## Batch Operations

### Bulk Insert vs Row-by-Row

```sql
-- ❌ BAD: Individual inserts in loop (slow)
INSERT INTO dbo.Discounts (CustomerId, Amount, FiscalYear) 
VALUES (1, 50, '2024');
INSERT INTO dbo.Discounts (CustomerId, Amount, FiscalYear) 
VALUES (2, 60, '2024');
INSERT INTO dbo.Discounts (CustomerId, Amount, FiscalYear) 
VALUES (3, 70, '2024');

-- ✅ GOOD: Batch insert (much faster)
INSERT INTO dbo.Discounts (CustomerId, Amount, FiscalYear)
VALUES 
    (1, 50, '2024'),
    (2, 60, '2024'),
    (3, 70, '2024');

-- ✅ BETTER: Bulk insert with staging table for large batches
BEGIN TRANSACTION;

INSERT INTO dbo.Discounts_Staging (CustomerId, Amount, FiscalYear)
SELECT CustomerId, Amount, FiscalYear FROM @DiscountsTable;

INSERT INTO dbo.Discounts (CustomerId, Amount, FiscalYear)
SELECT CustomerId, Amount, FiscalYear 
FROM dbo.Discounts_Staging;

COMMIT TRANSACTION;
```

### Bulk Updates

```sql
-- ❌ BAD: Update in loop
UPDATE dbo.Orders SET Status = 22 WHERE Id = 1;
UPDATE dbo.Orders SET Status = 22 WHERE Id = 2;
UPDATE dbo.Orders SET Status = 22 WHERE Id = 3;

-- ✅ GOOD: Bulk update using MERGE or UPDATE with CTE
MERGE INTO dbo.Orders AS target
USING (
    VALUES 
        (1, 22),
        (2, 22),
        (3, 22)
) AS source (Id, NewStatus)
ON target.Id = source.Id
WHEN MATCHED THEN
    UPDATE SET Status = source.NewStatus;
```

## Data Type Optimization

### Use Appropriate Data Types

```sql
-- ❌ BAD: Using NVARCHAR(MAX) for fixed-length data
CREATE TABLE dbo.CustomerData (
    CustomerId INT,
    CountryCode NVARCHAR(MAX),  -- Wasteful!
    Gender NVARCHAR(MAX)        -- Wasteful!
);

-- ✅ GOOD: Appropriate sizes, Unicode for names
CREATE TABLE dbo.Customers (
    Id INT PRIMARY KEY,
    CustomerNumber CHAR(6) NOT NULL,                    -- Fixed size
    Surname NVARCHAR(100) NOT NULL,                -- Unicode for names
    Forename NVARCHAR(100) NOT NULL,               -- Unicode
    MiddleName NVARCHAR(100) NULL,
    Gender CHAR(1) NOT NULL,                       -- M/F
    CustomerStatus INT NOT NULL,                    -- Use tinyint if range is 0-255
    DateOfBirth DATE NOT NULL,                     -- DATE not DATETIME for precision
    CreatedDate DATETIME2(7) NOT NULL,             -- DATETIME2 for better precision
    StoreId INT NOT NULL,
    FOREIGN KEY (StoreId) REFERENCES dbo.Stores(Id)
);

-- Index on numeric CustomerStatus is efficient
CREATE INDEX idx_customers_status ON dbo.Customers(CustomerStatus);
```

## Lock Optimization

### Avoid Deadlocks in Transactions

```sql
-- ❌ BAD: Lock order inconsistency causes deadlocks
-- Transaction A: Lock Orders, then Discounts
-- Transaction B: Lock Discounts, then Orders

-- ✅ GOOD: Consistent lock order (always same order)
BEGIN TRANSACTION;

-- Always acquire locks in same order: Discounts first, then Orders
UPDATE dbo.Discounts SET Amount = 60 WHERE Id = @DiscountId;
UPDATE dbo.Orders SET Status = 21 WHERE Id = @OrderId;

COMMIT TRANSACTION;

-- Use WITH (NOLOCK) for read-only queries to avoid blocking
SELECT c.Surname, o.Rating
FROM dbo.Customers c WITH (NOLOCK)
INNER JOIN dbo.Orders o WITH (NOLOCK) ON c.Id = o.CustomerId
WHERE c.StoreId = 1;
```

## Partitioning for Large Tables

### Partition by Period (Year)

```sql
-- ✅ GOOD: Partition large period-based tables (e.g. claims) by year for performance
CREATE PARTITION FUNCTION pf_claims_year (INT)
AS RANGE LEFT FOR VALUES (2022, 2023, 2024, 2025);

CREATE PARTITION SCHEME ps_claims_year
AS PARTITION pf_claims_year
TO ([PRIMARY], [PRIMARY], [PRIMARY], [PRIMARY], [PRIMARY]);

CREATE TABLE dbo.Claims (
    Id INT PRIMARY KEY,
    CustomerId INT NOT NULL,
    FiscalYear INT NOT NULL,
    ClaimPeriod NVARCHAR(20) NOT NULL,
    ClaimAmount DECIMAL(10, 2) NOT NULL,
    CreatedDate DATETIME2 NOT NULL
)
ON ps_claims_year(FiscalYear);

-- Queries filtering by year now scan only relevant partition
SELECT * FROM dbo.Claims
WHERE FiscalYear = 2024 AND ClaimPeriod = '2024-H1';
```

## Performance Monitoring Queries

### Find Slow Queries (SQL Server)

```sql
-- Top 10 slowest queries by average execution time
SELECT TOP 10
    qs.total_elapsed_time / qs.execution_count / 1000000 as avg_elapsed_ms,
    qs.execution_count,
    qs.total_physical_reads,
    SUBSTRING(
        st.text, 
        (qs.statement_start_offset / 2) + 1,
        (CASE qs.statement_end_offset 
            WHEN -1 THEN DATALENGTH(st.text)
            ELSE qs.statement_end_offset
        END - qs.statement_start_offset) / 2
    ) as query_text
FROM sys.dm_exec_query_stats qs
CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st
WHERE NOT (st.text LIKE '%sys.%' OR st.text LIKE '%dm_exec%')
ORDER BY avg_elapsed_ms DESC;
```

### Index Usage Analysis

```sql
-- Find unused indexes (wasting space and update time)
SELECT 
    OBJECT_NAME(i.object_id) as table_name,
    i.name as index_name,
    s.user_seeks,
    s.user_scans,
    s.user_lookups,
    s.user_updates
FROM sys.indexes i
LEFT JOIN sys.dm_db_index_usage_stats s 
    ON i.object_id = s.object_id 
    AND i.index_id = s.index_id
WHERE OBJECTPROPERTY(i.object_id, 'IsUserTable') = 1
    AND s.index_id IS NULL  -- No usage
ORDER BY s.user_updates DESC;
```

## Optimization Checklist

- [ ] Using explicit columns instead of SELECT *
- [ ] Proper index design (composite, covering, filtered)
- [ ] Window functions instead of correlated subqueries
- [ ] Early filtering in WHERE clause (avoid functions)
- [ ] Batch operations for bulk changes
- [ ] INNER JOINs preferred over LEFT JOINs when possible
- [ ] Reviewed execution plans for Table Scans
- [ ] Appropriate data types (NVARCHAR for Unicode names)
- [ ] DATETIME2(7) for temporal data
- [ ] Status and period (time-window) queries use indexed columns and covering indexes
- [ ] Monitored slow query log
- [ ] Index fragmentation checked and defragmented
- [ ] Statistics updated (`UPDATE STATISTICS`)
- [ ] No unnecessary indexes (bloating INSERT/UPDATE costs)

## Optimization Summary

**Frequent Lookup Queries**:
- Index: `[ParentId, Status] INCLUDE [Number, Surname, Forename]`
- Filter early by parent entity and status
- Use INNER JOINs for filtering

**Time-Sensitive Operations**:
- Preload period/validity tables (cache in application if possible)
- Window functions for averages and rankings
- Filtered indexes for frequently used status values

**Large Period-Based Tables**:
- Partition by year/period
- Covering indexes on the lookup columns
- Use cursor-based pagination for long lists

## Related Skills

- See **sql-code-review** for SQL security and correctness
- See **nhibernate** for ORM query patterns and NHibernate optimization
- See **backend-architecture** for application-layer query optimization

---

**Summary**: Optimize SQL Server queries by using explicit columns, strategic indexing, window functions, batch operations, and covering indexes. Prioritize filtering by the most selective columns and cover frequently used queries with dedicated indexes. Monitor execution plans regularly and use cursor-based pagination for large result sets.
