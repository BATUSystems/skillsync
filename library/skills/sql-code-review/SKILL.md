---
name: sql-code-review
description: SQL code review assistant for SQL Server focusing on security, performance, and schema best practices including SQL injection prevention, access controls, and anti-pattern detection
user-invokable: true
---

# SQL Server Code Review

Perform thorough SQL code review of your T-SQL/SQL Server code focusing on security, performance, maintainability, and schema design best practices.

## 🔒 Security Analysis

### SQL Injection Prevention

**❌ CRITICAL: SQL Injection vulnerability**
```sql
DECLARE @query NVARCHAR(MAX) = 'SELECT * FROM Customers WHERE Id = ' + @userId;
EXEC sp_executesql @query;
```

**✅ SECURE: Parameterized queries**
```sql
DECLARE @query NVARCHAR(MAX) = 'SELECT * FROM Customers WHERE Id = @id';
EXEC sp_executesql @query, N'@id INT', @id = @userId;
```

### Access Control & Permissions

- **Principle of Least Privilege**: Grant only necessary permissions (SELECT, INSERT, UPDATE per table)
- **Role-Based Access**: Use database roles (`db_datareader`, `db_datawriter`, custom roles) instead of direct user permissions
- **Schema Ownership**: Proper dbo schema ownership; avoid default schemas
- **Stored Procedure Security**: Set EXECUTE permissions on procedures, not underlying table permissions
- **View-Based Restrictions**: Use views to restrict column/row access for sensitive operations

### Data Protection

- **Sensitive Data**: Avoid SELECT * on tables holding personal data (contacts, identity documents)
- **PII Masking**: Consider views masking phone, email, document numbers except for authorized users
- **Audit Logging**: Ensure sensitive operations (financial changes, rating edits) are logged
- **Encryption**: Data in transit (connection encryption mandatory)

## ⚡ Performance Optimization

### Query Structure Analysis

**❌ BAD: Inefficient query**
```sql
SELECT DISTINCT c.* 
FROM Customers c, Discounts d, Stores s
WHERE c.Id = d.CustomerId 
  AND d.StoreId = s.Id
  AND YEAR(d.CreatedDate) = 2024;
```

**✅ GOOD: Optimized structure**
```sql
SELECT c.Id, c.FirstName, c.Surname, c.CustomerNumber
FROM Customers c
INNER JOIN Discounts d ON c.Id = d.CustomerId
WHERE d.CreatedDate >= '2024-01-01' 
  AND d.CreatedDate < '2025-01-01'
GROUP BY c.Id, c.FirstName, c.Surname, c.CustomerNumber;
```

### Index Strategy Review

- **Clustered Index**: PRIMARY KEY on CustomerId, DiscountId, etc.
- **Missing Indexes**: Look for frequently filtered columns (Status, CreatedDate, StoreId)
- **Composite Indexes**: Multi-column for common filter combinations
  - Example: `CREATE NONCLUSTERED INDEX idx_Customers_Status_Year ON Customers(Status, SignupYear)`
- **Index Maintenance**: Monitor fragmentation; rebuild if >10%, reorganize if 10-30%

### Join Optimization

- **Join Types**: INNER for mandatory relationships, LEFT for optional
- **Join Order**: SQL Server optimizer handles this, but understand query plans
- **No Cartesian Products**: Always verify join conditions
- **Subquery vs JOIN**: Use JOIN when joining Customers to Discounts; avoid correlated subqueries

**❌ BAD: Correlated subquery (N+1 risk)**
```sql
SELECT c.Id, 
       (SELECT COUNT(*) FROM Discounts d WHERE d.CustomerId = c.Id) as DiscountCount
FROM Customers c;
```

**✅ GOOD: JOIN with aggregation**
```sql
SELECT c.Id, COUNT(d.Id) as DiscountCount
FROM Customers c
LEFT JOIN Discounts d ON c.Id = d.CustomerId
GROUP BY c.Id;
```

### Window Functions for Analytics

Use window functions for running totals, rankings (rating, fiscal year):

```sql
SELECT 
    c.Id,
    c.Surname,
    o.Rating,
    ROW_NUMBER() OVER (PARTITION BY o.CategoryId ORDER BY o.Rating DESC) as RatingRank,
    RANK() OVER (PARTITION BY c.SignupYear ORDER BY o.Rating DESC) as YearRank
FROM Customers c
INNER JOIN Orders o ON c.Id = o.CustomerId;
```

## 🛠️ Code Quality & Maintainability

### SQL Formatting & Style

**❌ BAD: Poor formatting**
```sql
select c.id,c.surname,o.rating from customers c left join orders o on c.id=o.customerid where c.status='Active' and c.signup_year>=2020
```

**✅ GOOD: Clean formatting**
```sql
SELECT c.Id,
       c.Surname,
       o.Rating
FROM Customers c
LEFT JOIN Orders o ON c.Id = o.CustomerId
WHERE c.Status = 'Active'
  AND c.SignupYear >= 2020;
```

### Naming Conventions

- **Tables**: Plural, PascalCase (Customers, Discounts, Products)
- **Columns**: PascalCase (CustomerId, FirstName, CreatedDate)
- **Stored Procedures**: `usp_` prefix (e.g., `usp_GetCustomersByStore`)
- **Views**: `vw_` prefix (e.g., `vw_CustomerOrderSummary`)
- **Indexes**: `idx_` prefix (e.g., `idx_Customers_Status`)
- **Constraints**: `pk_`, `fk_`, `uq_` prefixes
- **Avoid**: Reserved words, single letter names, regional language in identifiers

### Schema Design Review

- **Normalization**: Use 3NF (most tables); denormalize only for performance with documentation
- **Data Types**: 
  - Use `NVARCHAR` for text (Unicode support for names)
  - Use `DATETIME2` for timestamps (better precision)
  - Use `BIGINT` for IDs if growth expected beyond 2 billion records
  - Use `DECIMAL(10,2)` for money/rating values
- **Constraints**: 
  - PRIMARY KEY on all tables
  - FOREIGN KEY for relationships (Customers → Stores)
  - NOT NULL on required columns
  - CHECK constraints for valid values (Status IN ('Active', 'Inactive'))
  - UNIQUE on natural keys (CustomerNumber, Email)
- **Default Values**: CreatedDate = GETUTCDATE(), IsDeleted = 0 for soft deletes

## 🗄️ Domain-Specific Database Patterns

Project-specific query patterns (domain filters, legacy table migration, reporting windows) belong in the project's own skill or instructions. When reviewing:

- Verify that user/tenant context filtering is applied consistently in queries that expose scoped data
- Verify that time-window (period/validity) conditions use indexed columns and range predicates
- Document migrations from legacy tables to modern ones, including the period when both are queried

## 🧪 Testing & Validation

### Data Integrity Checks

**Referential Integrity**: Check for orphaned records
```sql
-- Find customers with invalid store ID
SELECT c.Id, c.CustomerNumber 
FROM Customers c
LEFT JOIN Stores s ON c.StoreId = s.Id
WHERE s.Id IS NULL AND c.StoreId IS NOT NULL;
```

**Data Consistency**: Verify valid values
```sql
-- Check for invalid status values
SELECT DISTINCT Status 
FROM Customers 
WHERE Status NOT IN ('Active', 'Inactive', 'Archived', 'Suspended');

-- Check rating values (should be 1-5)
SELECT Id, Rating 
FROM Orders 
WHERE Rating < 1 OR Rating > 5;
```

**Temporal Consistency**: Verify date relationships
```sql
-- Check for order before registration
SELECT c.Id, c.RegisteredDate, o.OrderDate
FROM Customers c
INNER JOIN Orders o ON c.Id = o.CustomerId
WHERE o.OrderDate < c.RegisteredDate;
```

## 📊 Common Anti-Patterns to Avoid

### N+1 Query Problem (in application code calling SQL)

**❌ BAD**: App loads customers, then calls query per customer
```csharp
var customers = session.Query<Customer>().ToList();
foreach(var c in customers) {
    var discounts = session.Query<Discount>().Where(d => d.CustomerId == c.Id).ToList();
}
```

**✅ GOOD**: Single JOIN query
```csharp
var customerDiscounts = session.Query<Customer>()
    .Fetch(x => x.Discounts)  // NHibernate eager load
    .ToList();
```

### Overuse of DISTINCT

**❌ BAD**: DISTINCT hiding join issue
```sql
SELECT DISTINCT c.Surname 
FROM Customers c, Discounts d 
WHERE c.Id = d.CustomerId;
```

**✅ GOOD**: Proper GROUP BY
```sql
SELECT c.Surname
FROM Customers c
INNER JOIN Discounts d ON c.Id = d.CustomerId
GROUP BY c.Surname;
```

### Function Application on Columns Preventing Index Use

**❌ BAD**: YEAR() on column prevents index
```sql
SELECT * FROM Orders 
WHERE YEAR(OrderDate) = 2024;
```

**✅ GOOD**: Range condition uses index
```sql
SELECT * FROM Orders 
WHERE OrderDate >= '2024-01-01' 
  AND OrderDate < '2025-01-01';
```

### SELECT * Overhead

**❌ BAD**: Unnecessary columns
```sql
SELECT * FROM Customers c
INNER JOIN Discounts d ON c.Id = d.CustomerId;
```

**✅ GOOD**: Only needed columns
```sql
SELECT c.Id, c.Surname, d.Amount, d.Status
FROM Customers c
INNER JOIN Discounts d ON c.Id = d.CustomerId;
```

## 📋 SQL Review Checklist

### Security
- [ ] All user inputs are parameterized
- [ ] No dynamic SQL with string concatenation
- [ ] Appropriate access controls on sensitive tables
- [ ] PII data protected/masked where needed
- [ ] SQL injection vectors eliminated
- [ ] Audit logging for sensitive operations

### Performance
- [ ] Indexes exist for frequently filtered columns
- [ ] No SELECT * on large tables
- [ ] JOINs use appropriate types and optimization
- [ ] WHERE clauses are selective
- [ ] Subqueries optimized or converted to JOINs
- [ ] Window functions used appropriately
- [ ] No function calls on indexed columns

### Code Quality
- [ ] Consistent naming conventions (PascalCase, proper prefixes)
- [ ] SQL properly formatted and indented
- [ ] Meaningful comments for complex logic
- [ ] Appropriate data types (NVARCHAR, DATETIME2)
- [ ] Error handling in procedures (TRY/CATCH)

### Schema Design
- [ ] Tables normalized to 3NF
- [ ] Constraints enforce data integrity
- [ ] Indexes support query patterns
- [ ] Foreign key relationships defined
- [ ] Default values appropriate

### Domain Context
- [ ] Scoped data properly filtered by user/tenant context
- [ ] Time-window conditions handled correctly
- [ ] Legacy table migrations documented

## 🎯 Review Output Format

### Issue Template
```
## [PRIORITY] [CATEGORY]: [Brief Description]

**Location**: [Table/View/Procedure name and line]
**Issue**: [Detailed explanation]
**Security Risk**: [If applicable]
**Performance Impact**: [Query cost impact]
**Recommendation**: [Specific fix with code example]

**Before**:
```sql
-- Problematic SQL
```

**After**:
```sql
-- Improved SQL
```

**Expected Improvement**: [Performance gain or security benefit]
```

### Summary Assessment
- **Security Score**: [1-10] - SQL injection protection, access controls
- **Performance Score**: [1-10] - Query efficiency, index usage
- **Maintainability Score**: [1-10] - Code quality, documentation
- **Schema Quality Score**: [1-10] - Design patterns, consistency with project conventions

### Top 3 Priority Actions
1. **[Critical Security Fix]**: Address SQL injection vulnerabilities
2. **[Performance Optimization]**: Add missing indexes or optimize queries
3. **[Code Quality]**: Improve naming conventions and documentation

---

**Summary**: Prioritize parameterized queries, proper indexing, meaningful diagnostics, and clear formatting for maintainability.
