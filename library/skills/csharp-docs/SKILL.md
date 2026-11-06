---
name: csharp-docs
description: C# XML documentation comments best practices for .NET projects (methods, properties, classes, exceptions, code examples)
user-invokable: true
---

# C# XML Documentation

Guide for documenting C# code with XML comments in .NET projects. Covers `<summary>`, `<remarks>`, `<param>`, `<returns>`, `<exception>`, `<example>`, and proper documentation patterns for Clean Architecture layers, domain models, CQRS handlers, and repositories.

## Guidance for All APIs

### Summary (Required for Public Members)

- Use `<summary>` for a one-sentence description using present-tense, third-person verb
- Start with action verb (Gets, Sets, Creates, Validates, etc.)
- No need to state the return type in summary

```csharp
// ✅ Good: Clear, concise, action-oriented
/// <summary>
/// Gets the customer by their ID from the database.
/// </summary>
public async Task<Customer> GetByIdAsync(int customerId)
{
}

// ❌ Bad: Too verbose, repeats obvious
/// <summary>
/// This method gets a customer by ID. It returns a Task of Customer.
/// </summary>
public async Task<Customer> GetByIdAsync(int customerId)
{
}

// ✅ Good: Describes purpose, not "what it does"
/// <summary>
/// Validates whether a customer is eligible to place an order based on account status.
/// </summary>
public bool IsEligibleForOrder(CustomerStatus status)
{
}
```

### Remarks (Optional - Additional Context)

Use `<remarks>` for implementation details, important behavior, or business rules:

```csharp
/// <summary>
/// Gets customers registered with a store.
/// </summary>
/// <remarks>
/// <para>This method respects the user's authorization context:</para>
/// <list type="bullet">
///   <item><description>Store-scoped users can only see customers from their store</description></item>
///   <item><description>Users with full access can see all customers</description></item>
/// </list>
/// <para>Results are sorted by surname in ascending order.</para>
/// </remarks>
public async Task<List<Customer>> GetByStoreAsync(
    int storeId,
    CancellationToken cancellationToken = default)
{
}

// ✅ Business rule documentation
/// <summary>
/// Updates a customer's rating in the order record.
/// </summary>
/// <remarks>
/// Rating changes are only allowed during the designated change window configured for the fiscal year.
/// See the business rules documentation for more details on change window rules.
/// </remarks>
public async Task UpdateRatingAsync(int orderId, decimal rating)
{
}
```

### See and See Also References

- Use `<see cref>` to reference types/members **inline** within text
- Use `<seealso>` for standalone references in "See also" section

```csharp
/// <summary>
/// Creates a discount for a customer.
/// </summary>
/// <remarks>
/// This calls <see cref="IRepository{T}.SaveChangesAsync"/> to persist changes.
/// For related functionality, see <see cref="UpdateDiscountAsync"/>.
/// </remarks>
/// <seealso cref="DeleteDiscountAsync"/>
/// <seealso cref="GetDiscountAsync"/>
public async Task<Discount> CreateDiscountAsync(Discount discount)
{
}
```

### Inline Code and Keywords

- Use `<c>` for inline code (variable names, methods, properties)
- Use `<see langword>` for C# keywords (`null`, `true`, `false`, `default`)
- Use `<code>` with `language` attribute for code blocks

```csharp
/// <summary>
/// Sets the customer status to <c>placed</c>.
/// </summary>
/// <remarks>
/// If the customer status is <see langword="null"/>, this method throws an exception.
/// Calling it again has no effect.
/// </remarks>
public void MarkAsPlaced()
{
}

/// <example>
/// <code language="csharp">
/// var handler = new GetCustomersQueryHandler(repository);
/// var query = new GetCustomersQuery { StoreId = 1 };
/// var customers = await handler.Handle(query, cancellationToken);
/// </code>
/// </example>
```

## Classes and Types

### Class Documentation

```csharp
/// <summary>
/// Represents a customer registered with a store.
/// </summary>
/// <remarks>
/// <para>The <c>Customer</c> class encapsulates customer information and order state.</para>
/// <para>Key properties:</para>
/// <list type="bullet">
///   <item><c>CustomerNumber</c>: Unique identifier (store-defined format)</item>
///   <item><c>Surname</c>, <c>Forename</c>: Personal information with Unicode support</item>
///   <item><c>Status</c>: Current customer status</item>
///   <item><c>Orders</c>: Collection of orders</item>
/// </list>
/// </remarks>
public class Customer : DomainEntity
{
    public string CustomerNumber { get; set; }
    public string Surname { get; set; }
    public string Forename { get; set; }
    public CustomerStatus Status { get; set; }
    public ICollection<Order> Orders { get; set; }
}

/// <summary>
/// Handles the command to create a new discount for a customer.
/// </summary>
/// <remarks>
/// This is a CQRS command handler that adheres to Clean Architecture principles.
/// It validates the input, creates the discount in the domain model, and persists it.
/// </remarks>
public class CreateDiscountCommandHandler : IRequestHandler<CreateDiscountCommand, int>
{
    // Implementation
}

/// <summary>
/// Provides data access operations for <see cref="Customer"/> entities.
/// </summary>
/// <remarks>
/// Implements the Repository pattern using NHibernate for ORM.
/// All database operations are asynchronous and support cancellation tokens.
/// </remarks>
public class CustomerRepository : IRepository<Customer>
{
    // Implementation
}
```

## Methods

### Method Documentation

```csharp
/// <summary>
/// Gets a customer by their unique ID.
/// </summary>
/// <param name="customerId">The unique numeric identifier of the customer.</param>
/// <param name="cancellationToken">A cancellation token that can be used to cancel the operation.</param>
/// <returns>
/// A task that represents the asynchronous operation.
/// The task result contains the <see cref="Customer"/> if found; otherwise, <see langword="null"/>.
/// </returns>
/// <exception cref="ArgumentException">Thrown when <paramref name="customerId"/> is less than or equal to zero.</exception>
public async Task<Customer> GetByIdAsync(int customerId, CancellationToken cancellationToken = default)
{
}

/// <summary>
/// Validates whether the customer is eligible to place an order.
/// </summary>
/// <param name="fiscalYear">The fiscal year in YYYY format (e.g., "2024").</param>
/// <returns>
/// <see langword="true"/> if the customer is eligible to place an order; otherwise, <see langword="false"/>.
/// </returns>
/// <remarks>
/// Eligibility is determined by:
/// <list type="number">
///   <item><description>Customer status must be <c>Active</c> or <c>Placed</c></description></item>
///   <item><description>Customer must not have closed the account</description></item>
///   <item><description>There must be no conflicting order for the same fiscal year</description></item>
/// </list>
/// </remarks>
public bool IsEligibleForOrder(string fiscalYear)
{
}

/// <summary>
/// Creates a claim for a shipping discount.
/// </summary>
/// <param name="claimPeriod">The claim period (e.g., "2024-H1" or "2025-H2").</param>
/// <param name="claimAmount">The amount being claimed in the local currency.</param>
/// <param name="cancellationToken">A cancellation token that can be used to cancel the operation.</param>
/// <returns>The ID of the newly created claim.</returns>
/// <exception cref="InvalidOperationException">
/// Thrown when the customer has already submitted a claim for the specified claim period.
/// </exception>
/// <exception cref="ArgumentOutOfRangeException">
/// Thrown when <paramref name="claimAmount"/> is not positive.
/// </exception>
public async Task<int> CreateDiscountClaimAsync(
    string claimPeriod,
    decimal claimAmount,
    CancellationToken cancellationToken = default)
{
}
```

### Parameter Documentation

Describe parameters clearly without restating the type:

```csharp
/// <param name="customerId">The unique numeric identifier of the customer.</param>
/// <param name="includeOrders">
/// <see langword="true"/> to load related orders; otherwise, <see langword="false"/>.
/// The default is <see langword="false"/>.
/// </param>
/// <param name="statuses">
/// A bitwise combination of the <see cref="CustomerStatus"/> values that specifies which
/// customers to include in the results. If not specified, all active customers are included.
/// </param>

// ❌ Bad: Restates obvious information
/// <param name="customerId">An int representing the customer ID.</param>
/// <param name="flag">A boolean flag indicating whether to include children.</param>
```

### Return Documentation

```csharp
/// <returns>
/// A collection of <see cref="Customer"/> entities matching the specified criteria.
/// The collection may be empty if no customers match the criteria.
/// </returns>

/// <returns>
/// <see langword="true"/> if the rating is within the valid range (1-5); otherwise, <see langword="false"/>.
/// </returns>

/// <returns>
/// When this method returns, contains the <see cref="CustomerDto"/> if found; otherwise, <see langword="null"/>.
/// This parameter is treated as uninitialized.
/// </returns>
```

## Constructors

### Constructor Documentation

Use standard wording: "Initializes a new instance of the [Class] class [or struct]"

```csharp
/// <summary>
/// Initializes a new instance of the <see cref="Customer"/> class.
/// </summary>
/// <param name="customerNumber">The unique customer number (store-defined format).</param>
/// <param name="surname">The customer's surname with Unicode support for international names.</param>
/// <param name="forename">The customer's given name(s).</param>
/// <param name="status">The initial customer <see cref="CustomerStatus"/>.</param>
public Customer(string customerNumber, string surname, string forename, CustomerStatus status)
{
}

/// <summary>
/// Initializes a new instance of the <see cref="CustomerRepository"/> class.
/// </summary>
/// <param name="session">The NHibernate session used for database operations.</param>
/// <exception cref="ArgumentNullException">Thrown when <paramref name="session"/> is <see langword="null"/>.</exception>
public CustomerRepository(ISession session)
{
}
```

## Properties

### Property Documentation

- Read-write: "Gets or sets..."
- Read-only: "Gets..."
- Boolean: "Gets [or sets] a value that indicates whether..."

```csharp
/// <summary>
/// Gets or sets the customer's unique number.
/// </summary>
/// <value>
/// A string representing the customer serial number (e.g., "223456").
/// </value>
public string CustomerNumber { get; set; }

/// <summary>
/// Gets the date when the order was created.
/// </summary>
/// <value>
/// A <see cref="DateTime"/> in UTC representing the order creation timestamp. The default is the current UTC time.
/// </value>
public DateTime CreatedAt { get; private set; } = DateTime.UtcNow;

/// <summary>
/// Gets a value that indicates whether the customer has closed their account.
/// </summary>
/// <value>
/// <see langword="true"/> if the customer's status is <c>Archived</c>; otherwise, <see langword="false"/>.
/// </value>
public bool IsArchived => Status == CustomerStatus.Archived;

/// <summary>
/// Gets or sets the collection of orders for this customer.
/// </summary>
/// <value>
/// A collection of <see cref="Order"/> entities. The default is an empty collection.
/// </value>
/// <remarks>
/// This property is loaded lazily by NHibernate. Use <c>.Fetch(c => c.Orders)</c>
/// in queries to eagerly load orders and avoid N+1 queries.
/// </remarks>
public ICollection<Order> Orders { get; set; } = new List<Order>();
```

## Exceptions

### Exception Documentation

Document exceptions thrown directly by the member:

```csharp
/// <exception cref="ArgumentNullException">
/// Thrown when <paramref name="customerId"/> is <see langword="null"/>.
/// </exception>
/// <exception cref="CustomerNotFoundException">
/// Thrown when a customer with the specified ID is not found in the database.
/// </exception>
/// <exception cref="InvalidOperationException">
/// Thrown when the customer's status does not allow order changes.
/// </exception>
public async Task PlaceOrderAsync(int customerId)
{
}

// ❌ Bad: Generic phrasing
/// <exception cref="Exception">Thrown in case of error.</exception>

// ✅ Good: Specific condition
/// <exception cref="ArgumentOutOfRangeException">
/// Thrown when <paramref name="rating"/> is outside the range 1-5.
/// </exception>
```

## Examples

### Example Usage

Use `<example>` to show how to use the member:

```csharp
/// <summary>
/// Gets all customers registered with a store.
/// </summary>
/// <example>
/// <code language="csharp">
/// var repository = new CustomerRepository(nhSession);
/// var customers = await repository.GetByStoreAsync(
///     storeId: 1,
///     cancellationToken: CancellationToken.None);
///
/// foreach (var customer in customers)
/// {
///     Console.WriteLine($"{customer.Surname}, {customer.Forename}");
/// }
/// </code>
/// </example>
public async Task<List<Customer>> GetByStoreAsync(
    int storeId,
    CancellationToken cancellationToken = default)
{
}

/// <summary>
/// Handles the command to update a customer's rating during the allowed change window.
/// </summary>
/// <example>
/// <para>Typical usage in a MediatR handler:</para>
/// <code language="csharp">
/// var handler = new UpdateCustomerRatingCommandHandler(
///     orderRepository,
///     unitOfWork);
///
/// var command = new UpdateCustomerRatingCommand
/// {
///     OrderId = 42,
///     Rating = 4.5m
/// };
///
/// await handler.Handle(command, cancellationToken);
/// </code>
/// </example>
public class UpdateCustomerRatingCommandHandler : IRequestHandler<UpdateCustomerRatingCommand>
{
}
```

## Documentation Best Practices Checklist

- [ ] All public types have `<summary>`
- [ ] All public methods have `<summary>`, `<param>`, and `<returns>`
- [ ] All exceptions documented with `<exception>`
- [ ] Important business rules documented in `<remarks>`
- [ ] Authorization context behavior documented (restricted vs full-access roles)
- [ ] Complex properties have `<remarks>` explaining lazy loading or relationships
- [ ] Generic type parameters documented with `<typeparam>`
- [ ] Links to related members use `<see cref>` or `<seealso>`
- [ ] C# keywords use `<see langword>`
- [ ] Inline code uses `<c>` tags
- [ ] Examples provided for public APIs using `<example>` and `<code>`
- [ ] Boolean properties/returns use standard phrasing

## IntelliSense Support

Proper XML documentation enables IntelliSense:

```
When hovering over Customer.GetByIdAsync():
┌─────────────────────────────────────────┐
│ Task<Customer> GetByIdAsync(int, Cancel) │
│                                         │
│ Gets a customer by their unique ID.      │
│                                         │
│ Parameters:                             │
│   customerId (int)                       │
│   The unique numeric identifier of the  │
│   customer.                              │
│                                         │
│ Returns:                                │
│   The Customer if found; otherwise null  │
└─────────────────────────────────────────┘
```

## Related Skills

- See **dotnet-best-practices** for code structure that documentation describes
- See **backend-architecture** for Clean Architecture layer patterns
- See **csharp-xunit** for documenting test helper methods

---

**Summary**: Document all public APIs with clear `<summary>` tags, use `<param>` and `<returns>` for methods, document exceptions with `<exception>`, use `<remarks>` for business rules and domain context (authorization, validity windows), provide `<example>` code, and enable rich IntelliSense experience for developers.
