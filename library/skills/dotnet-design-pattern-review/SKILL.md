---
name: dotnet-design-pattern-review
description: Design pattern review for .NET projects (CQRS, Repository, Factory, Provider patterns) with SOLID principles, Clean Architecture alignment, and architectural decision validation
user-invokable: true
---

# .NET/C# Design Pattern Review

Review C#/.NET code for design pattern implementation, architectural alignment, and improvement suggestions. Identifies missing patterns, anti-patterns, and opportunities for better architecture.

## Architecture Patterns

### Core Patterns (Must Implement)

**CQRS (Command Query Responsibility Segregation)** with MediatR
- Command handlers for write operations: `IRequestHandler<TCommand, TResult>`
- Query handlers for read operations: `IRequestHandler<TQuery, TResult>`
- Clear separation between commands (create/update/delete) and queries (read)
- Example: `CreateDiscountCommand` → `CreateDiscountCommandHandler`, `GetCustomersQuery` → `GetCustomersQueryHandler`

**Repository Pattern** with NHibernate
- Async data access interfaces: `IRepository<T>`
- Implement: `GetByIdAsync()`, `GetAll()`, `Add()`, `Update()`, `Delete()`, `SaveChangesAsync()`
- Query objects / Specifications for complex queries: `ISpecification<T>`
- Single responsibility: Data access abstraction only

**Dependency Injection** - Clean Architecture layers
- Constructor injection with null checks: `ArgumentNullException.ThrowIfNull()`
- Primary constructor syntax: `public class MyService(IRepository<T> repo)`
- Service lifetimes: Singleton (SessionFactory), Scoped (Repositories, Handlers), Transient (Services)
- Interface-based abstractions in all layers

**Provider Pattern** for External Services
- Database provider (NHibernate via ISession)
- Configuration provider (IConfiguration)
- Logger provider (ILogger<T>)
- Clear contracts for external dependencies

**Factory Pattern** for Complex Object Creation
- Used for: Creating aggregate roots, complex entities
- Example: `Discount.Create(customerId, amount, year)` factory method
- Encapsulates validation and business rule enforcement
- Reduces coupling to concrete constructors

### Advanced Patterns

**Specification Pattern** for Reusable Queries
```csharp
public interface ISpecification<T>
{
    IQueryable<T> Apply(IQueryable<T> query);
}

// Usage
var spec = new CustomersByStoreSpecification(storeId, status);
var customers = await repository.GetAsync(spec);
```

**Unit of Work Pattern**
- Coordinate multiple repository saves
- Single `SaveChangesAsync()` call at end of transaction
- `IRepository<T>.UnitOfWork.SaveChangesAsync()`

**Service Locator (ANTI-PATTERN)** ❌ Avoid!
- Don't use `IServiceProvider.GetService()`
- Don't resolve in MediatR handlers
- Use constructor injection instead

## Clean Architecture Layers Review

### Domain Layer (`<Solution>.Domain/`)
✅ **Should contain**:
- Entity classes (aggregate roots): `Customer`, `Discount`, `Order`
- Domain interfaces (no implementation): `IRepository<T>`, `ICustomerService`
- Domain events: `OrderPlacedEvent`
- Specifications
- No dependencies on other layers

❌ **Should NOT contain**:
- NHibernate mappings
- Database queries
- External service calls
- Controllers or HTTP concerns

### Application Layer (`<Solution>.Application/`)
✅ **Should contain**:
- MediatR Handlers (Commands & Queries)
- DTOs (Data Transfer Objects)
- Application services (orchestration)
- AutoMapper profiles
- Application interfaces

❌ **Should NOT contain**:
- Database implementation details
- HTTP concerns
- External service implementations

### Infrastructure Layer (`<Solution>.Infrastructure/`)
✅ **Should contain**:
- NHibernate SessionFactory configuration
- Entity mappings (`ClassMap<T>`)
- Repository implementations
- External service implementations

❌ **Should NOT contain**:
- Business logic (belongs in domain or application)
- Controllers

### API Layer (`<Solution>.API/`)
✅ **Should contain**:
- Controllers
- Request/Response models
- Middleware
- Startup configuration

❌ **Should NOT contain**:
- Business logic
- Data access code

## Design Pattern Implementation Review

### CQRS Review Checklist

**Command Handlers**
- [ ] Inherits `IRequestHandler<TCommand, TResult>`
- [ ] Constructor has null-checked dependencies
- [ ] Validates input (domain validation, not just ModelState)
- [ ] Creates aggregate root using factory methods
- [ ] Calls `repository.SaveChangesAsync()` at end
- [ ] Logs significant events (creation, deletion)
- [ ] Returns meaningful result (entity ID, DTO)
- [ ] Handles exceptions gracefully (CustomerNotFoundException, etc.)

```csharp
// ✅ Good Command Handler
public class CreateDiscountCommandHandler : IRequestHandler<CreateDiscountCommand, int>
{
    private readonly IRepository<Discount> _repository;
    private readonly ILogger<CreateDiscountCommandHandler> _logger;

    public CreateDiscountCommandHandler(IRepository<Discount> repository, ILogger<CreateDiscountCommandHandler> logger)
    {
        _repository = repository ?? throw new ArgumentNullException(nameof(repository));
        _logger = logger ?? throw new ArgumentNullException(nameof(logger));
    }

    public async Task<int> Handle(CreateDiscountCommand request, CancellationToken ct)
    {
        var discount = Discount.Create(request.CustomerId, request.Amount, request.Year);
        _repository.Add(discount);
        await _repository.SaveChangesAsync();
        _logger.LogInformation("Discount created for customer {CustomerId}", request.CustomerId);
        return discount.Id;
    }
}
```

**Query Handlers**
- [ ] Inherits `IRequestHandler<TQuery, TResult>`
- [ ] Returns DTOs (not domain entities)
- [ ] Uses projection (`.Select()`) for efficiency
- [ ] No side effects (read-only)
- [ ] Efficient querying (no N+1 problems)
- [ ] Handles authorization/filtering (store, status)

```csharp
// ✅ Good Query Handler
public class GetCustomersByStoreQueryHandler 
    : IRequestHandler<GetCustomersByStoreQuery, List<CustomerDto>>
{
    private readonly IRepository<Customer> _repository;

    public GetCustomersByStoreQueryHandler(IRepository<Customer> repository)
    {
        _repository = repository ?? throw new ArgumentNullException(nameof(repository));
    }

    public async Task<List<CustomerDto>> Handle(GetCustomersByStoreQuery request, CancellationToken ct)
    {
        var query = _repository.GetAll()
            .Where(c => c.StoreId == request.StoreId);

        if (request.Status.HasValue)
            query = query.Where(c => c.Status == request.Status);

        return await query
            .Select(c => new CustomerDto { Id = c.Id, Surname = c.Surname })
            .ToListAsync(ct);
    }
}
```

### Repository Pattern Review

- [ ] Implements generic `IRepository<T>` interface
- [ ] All methods are async (`GetByIdAsync`, `SaveChangesAsync`)
- [ ] No business logic (data access only)
- [ ] Queries composed in handlers/specifications, not in repository
- [ ] Proper use of `NHibernate.Linq` for LINQ-to-NHibernate
- [ ] N+1 queries prevented with `.Fetch()`

❌ **Anti-pattern: Repository with business logic**
```csharp
// BAD: Repository doing business logic
public class CustomerRepository
{
    public async Task PlaceOrder(int customerId)
    {
        // This is business logic, not data access!
        var customer = await GetByIdAsync(customerId);
        customer.Status = CustomerStatus.Placed;
        await SaveChangesAsync();
    }
}
```

✅ **Good: Business logic in handlers, data access in repository**
```csharp
// Handler orchestrates business logic
public class PlaceOrderCommandHandler : IRequestHandler<PlaceOrderCommand>
{
    public async Task Handle(PlaceOrderCommand request, CancellationToken ct)
    {
        var customer = await _repository.GetByIdAsync(request.CustomerId);
        customer.PlaceOrder(); // Domain method with business rules
        await _repository.SaveChangesAsync();
    }
}
```

### Dependency Injection Review

- [ ] Services registered with correct lifetimes
  - Singleton: `SessionFactory` (expensive to create)
  - Scoped: Repositories, Handlers (per-request)
  - Transient: Specific services (stateless utilities)
- [ ] No service locator pattern (IServiceProvider in handlers)
- [ ] Constructor dependencies are required (null-check)
- [ ] Interfaces used for all dependencies (testability)
- [ ] Optional dependencies marked as `.Optional()` if allowed

```csharp
// ✅ Good: Dependency injection with null checks
public class CustomerOrderHandler : IRequestHandler<PlaceOrderCommand>
{
    private readonly IRepository<Customer> _customerRepository;
    private readonly IRepository<Order> _orderRepository;
    private readonly ILogger<CustomerOrderHandler> _logger;

    public CustomerOrderHandler(
        IRepository<Customer> customerRepository,
        IRepository<Order> orderRepository,
        ILogger<CustomerOrderHandler> logger)
    {
        _customerRepository = customerRepository ?? throw new ArgumentNullException(nameof(customerRepository));
        _orderRepository = orderRepository ?? throw new ArgumentNullException(nameof(orderRepository));
        _logger = logger ?? throw new ArgumentNullException(nameof(logger));
    }
}
```

## SOLID Principles Validation

### Single Responsibility
- Each handler has ONE reason to change (one command/query)
- Each repository handles ONE entity type
- Violation example: Handler doing both business logic AND email sending

### Open/Closed
- Extend with new handlers, not modify existing
- Use specification pattern for new query types
- Violation: Adding if-else logic to existing handlers

### Liskov Substitution
- All `IRepository<T>` implementations behave consistently
- Specifications implement `ISpecification<T>` consistently
- Don't break contracts in derived classes

### Interface Segregation
- Don't force handlers to implement unused methods
- `IRequestHandler<TRequest, TResponse>` is focused
- Violation: Creating bloated interfaces with 20 methods

### Dependency Inversion
- Depend on interfaces, not concrete classes
- Inject `IRepository<T>`, not `CustomerRepository`
- Violation: `new CustomerRepository()` in handlers

## Design Pattern Anti-Patterns to Avoid

### ❌ Anemic Domain Model
```csharp
// BAD: Entity with no behavior
public class Customer
{
    public int Id { get; set; }
    public string Surname { get; set; }
    // No methods!
}

// Handler does all work
public class PlaceOrderCommandHandler
{
    public async Task Handle(PlaceOrderCommand request, CancellationToken ct)
    {
        var customer = await _repository.GetByIdAsync(request.CustomerId);
        customer.LastOrderDate = DateTime.Now; // Handler setting properties
        customer.Status = CustomerStatus.Placed;
        await _repository.SaveChangesAsync();
    }
}
```

### ✅ Rich Domain Model
```csharp
// GOOD: Entity with behavior
public class Customer
{
    public int Id { get; private set; }
    public string Surname { get; private set; }
    
    public void PlaceOrder(string fiscalYear)
    {
        if (Status != CustomerStatus.Active)
            throw new InvalidOperationException("Cannot place order for inactive customer");
        LastOrderDate = DateTime.Now;
        Status = CustomerStatus.Placed;
    }
}

// Handler orchestrates
public class PlaceOrderCommandHandler
{
    public async Task Handle(PlaceOrderCommand request, CancellationToken ct)
    {
        var customer = await _repository.GetByIdAsync(request.CustomerId);
        customer.PlaceOrder(request.FiscalYear); // Domain method
        await _repository.SaveChangesAsync();
    }
}
```

### ❌ Service Locator Pattern
```csharp
// BAD: Using IServiceProvider to resolve
public class MyHandler : IRequestHandler<MyCommand>
{
    private readonly IServiceProvider _serviceProvider;
    
    public async Task Handle(MyCommand request, CancellationToken ct)
    {
        var repository = _serviceProvider.GetService<IRepository<Customer>>(); // Anti-pattern!
    }
}
```

### ✅ Explicit Dependency Injection
```csharp
// GOOD: All dependencies in constructor
public class MyHandler : IRequestHandler<MyCommand>
{
    private readonly IRepository<Customer> _repository;
    
    public MyHandler(IRepository<Customer> repository)
    {
        _repository = repository ?? throw new ArgumentNullException(nameof(repository));
    }
}
```

## Design Pattern Review Checklist

### Architecture & Organization
- [ ] Project structure follows Clean Architecture (Domain/Application/Infrastructure/API)
- [ ] Namespaces reflect layer structure
- [ ] Separation of concerns maintained
- [ ] No circular dependencies

### CQRS Implementation
- [ ] Distinct Command and Query handlers
- [ ] Clear separation between write and read paths
- [ ] Handlers register with MediatR correctly
- [ ] Commands return appropriate results

### Repository Pattern
- [ ] Generic `IRepository<T>` with async methods
- [ ] No business logic in repositories
- [ ] Queries composed cleanly
- [ ] Specifications for complex queries

### Dependency Injection
- [ ] Constructor injection with null checks
- [ ] Appropriate service lifetimes
- [ ] No service locator pattern
- [ ] Interface-based dependencies

### SOLID Principles
- [ ] Single Responsibility (handlers focus on one concern)
- [ ] Open/Closed (extend with new handlers, don't modify existing)
- [ ] Liskov Substitution (interfaces honored consistently)
- [ ] Interface Segregation (focused interfaces)
- [ ] Dependency Inversion (depend on abstractions)

### Domain Model
- [ ] Rich behavior in domain entities (not anemic)
- [ ] Business rules enforced in domain methods
- [ ] Factory methods for object creation
- [ ] Clear ubiquitous language

### Code Quality
- [ ] Consistent error handling
- [ ] Structured logging
- [ ] Meaningful exception types
- [ ] Clear naming reflecting domain concepts

## Improvement Recommendations Format

When reviewing existing code:

```
## [PATTERN NAME]: [Issue]

**Current Implementation**: [Describe current pattern]

**Issue**: [Explain pattern violation or improvement opportunity]

**Recommendation**: [Specific improvement with code example]

**Before**:
```csharp
// Current code
```

**After**:
```csharp
// Improved code
```

**Benefit**: [Performance, maintainability, or correctness improvement]
```

---

**Summary**: Review design patterns in context of Clean Architecture, ensure CQRS handlers are properly separated, verify repository pattern discipline, enforce dependency injection rigorously, validate SOLID principles, maintain rich domain models, and apply domain-specific patterns for authorization and business rules.
