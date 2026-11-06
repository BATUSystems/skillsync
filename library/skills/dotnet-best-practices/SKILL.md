---
name: dotnet-best-practices
description: .NET/C# best practices including Clean Architecture, NHibernate repositories, CQRS patterns, dependency injection, error handling, and code quality standards
user-invokable: true
---

# .NET/C# Best Practices

Ensure .NET/C# code meets best practices specific to Clean Architecture, NHibernate ORM, CQRS with MediatR, and quality standards.

## Object-Oriented Design & SOLID Principles

### Clean Code Fundamentals
- **Single Responsibility Principle**: Each class has one reason to change
- **Open/Closed Principle**: Open for extension (interfaces), closed for modification
- **Liskov Substitution**: Derived classes substitute base classes without breaking
- **Interface Segregation**: Many specific interfaces vs one general-purpose
- **Dependency Inversion**: Depend on abstractions (interfaces), not concretions

### Naming & Readability

**Classes & Methods**: PascalCase, clear intent
```csharp
// ❌ Bad: Vague names
class Process { void Do() { } }

// ✅ Good: Clear intent
class CustomerOrderProcessor { void PlaceOrderForFiscalYear() { } }
```

**Variables**: camelCase, meaningful names
```csharp
// ❌ Bad: Single letter, no context
int s; decimal a; var x;

// ✅ Good: Clear purpose
int customerId; decimal discountAmount; var orderYear;
```

## Clean Architecture Integration

### Layer Structure

**Domain Layer** (`<Solution>.Domain/`)
- Entity classes (Customer, Discount, Order)
- Domain interfaces (no implementation)
- Domain events, specifications
- NO dependencies on other layers

**Application Layer** (`<Solution>.Application/`)
- Use cases/Handlers (MediatR handlers)
- DTOs (Data Transfer Objects)
- Application interfaces
- Depends on Domain only (not Infrastructure)

**Infrastructure Layer** (`<Solution>.Infrastructure/`)
- NHibernate mappings, configuration
- Repository implementations
- Persistence abstractions
- Depends on Domain and Application

**API Layer** (`<Solution>.API/`)
- Controllers
- Request/Response models
- Middleware
- Depends on all layers appropriately

### Dependency Direction

```
API
 ↓
Application (MediatR handlers, services)
 ↓
Domain (Entities, interfaces)
 ↑
Infrastructure (Repositories, NHibernate)
```

**Cross-cutting concerns** (logging, caching) are injected into higher layers.

## CQRS & MediatR Pattern

### Command Handlers (Write Operations)

```csharp
// Command: Immutable DTO for creating discount
public class CreateDiscountCommand : IRequest<int>
{
    public int CustomerId { get; init; }
    public decimal Amount { get; init; }
    public string FiscalYear { get; init; }
}

// Handler: Business logic for creating discount
public class CreateDiscountCommandHandler : IRequestHandler<CreateDiscountCommand, int>
{
    private readonly IRepository<Discount> _discountRepository;
    private readonly ILogger<CreateDiscountCommandHandler> _logger;

    public CreateDiscountCommandHandler(
        IRepository<Discount> discountRepository,
        ILogger<CreateDiscountCommandHandler> logger)
    {
        _discountRepository = discountRepository ?? throw new ArgumentNullException(nameof(discountRepository));
        _logger = logger ?? throw new ArgumentNullException(nameof(logger));
    }

    public async Task<int> Handle(CreateDiscountCommand request, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(request);

        // Business logic/validation
        var discount = Discount.Create(request.CustomerId, request.Amount, request.FiscalYear);
        
        _discountRepository.Add(discount);
        await _discountRepository.UnitOfWork.SaveChangesAsync(cancellationToken);

        _logger.LogInformation("Discount created for customer {CustomerId}", request.CustomerId);
        
        return discount.Id;
    }
}
```

### Query Handlers (Read Operations)

```csharp
// Query: Immutable DTO for fetching customers
public class GetCustomersByStoreQuery : IRequest<List<CustomerDto>>
{
    public int StoreId { get; init; }
    public CustomerStatus? Status { get; init; }
}

// Handler: Efficient read with projection
public class GetCustomersByStoreQueryHandler 
    : IRequestHandler<GetCustomersByStoreQuery, List<CustomerDto>>
{
    private readonly IRepository<Customer> _customerRepository;

    public GetCustomersByStoreQueryHandler(IRepository<Customer> customerRepository)
    {
        _customerRepository = customerRepository ?? throw new ArgumentNullException(nameof(customerRepository));
    }

    public async Task<List<CustomerDto>> Handle(
        GetCustomersByStoreQuery request, 
        CancellationToken cancellationToken)
    {
        var query = _customerRepository.GetAll()
            .Where(c => c.StoreId == request.StoreId);

        if (request.Status.HasValue)
            query = query.Where(c => c.Status == request.Status);

        return await query
            .Select(c => new CustomerDto
            {
                Id = c.Id,
                CustomerNumber = c.CustomerNumber,
                Surname = c.Surname,
                FirstName = c.FirstName,
                Status = c.Status.ToString()
            })
            .ToListAsync(cancellationToken);
    }
}
```

## NHibernate & Repository Pattern

### Repository Implementation

```csharp
public interface IRepository<T> where T : class
{
    Task<T> GetByIdAsync(int id);
    IQueryable<T> GetAll();
    void Add(T entity);
    void Update(T entity);
    void Delete(T entity);
    Task<int> SaveChangesAsync();
}

public class Repository<T> : IRepository<T> where T : class
{
    protected readonly ISession _session;

    public Repository(ISession session)
    {
        _session = session ?? throw new ArgumentNullException(nameof(session));
    }

    public async Task<T> GetByIdAsync(int id)
    {
        return await _session.GetAsync<T>(id);
    }

    public IQueryable<T> GetAll()
    {
        return _session.Query<T>();
    }

    public void Add(T entity)
    {
        ArgumentNullException.ThrowIfNull(entity);
        _session.SaveOrUpdate(entity);
    }

    public void Delete(T entity)
    {
        ArgumentNullException.ThrowIfNull(entity);
        _session.Delete(entity);
    }

    public async Task<int> SaveChangesAsync()
    {
        await _session.FlushAsync();
        return 1; // NHibernate doesn't return affected rows count easily
    }
}
```

### Query Objects & Specifications

For complex queries, use Query Objects pattern:

```csharp
public interface ISpecification<T>
{
    IQueryable<T> Apply(IQueryable<T> query);
}

// Usage in repository
public class CustomersByStoreSpecification : ISpecification<Customer>
{
    private readonly int _storeId;
    private readonly CustomerStatus? _status;

    public CustomersByStoreSpecification(int storeId, CustomerStatus? status = null)
    {
        _storeId = storeId;
        _status = status;
    }

    public IQueryable<Customer> Apply(IQueryable<Customer> query)
    {
        query = query.Where(c => c.StoreId == _storeId);

        if (_status.HasValue)
            query = query.Where(c => c.Status == _status);

        return query;
    }
}

// In repository
public async Task<List<T>> GetAsync(ISpecification<T> spec)
{
    return await spec.Apply(_session.Query<T>()).ToListAsync();
}
```

## Dependency Injection & Configuration

### Service Registration (Startup/Program.cs)

```csharp
// .NET 6+ Minimal APIs / Program.cs
var builder = WebApplication.CreateBuilder(args);

// Infrastructure
builder.Services.AddSingleton(sp => CreateSessionFactory(builder.Configuration));
builder.Services.AddScoped(sp => sp.GetRequiredService<ISessionFactory>().OpenSession());
builder.Services.AddScoped(typeof(IRepository<>), typeof(Repository<>));

// Application (MediatR)
builder.Services.AddMediatR(cfg => cfg.RegisterServicesFromAssembly(typeof(Program).Assembly));

// Domain
builder.Services.AddAutoMapper(typeof(Program).Assembly);

// API
builder.Services.AddControllers();
builder.Services.AddLogging();

var app = builder.Build();
// ... middleware

app.MapControllers();
app.Run();
```

### Constructor Injection Best Practices

```csharp
// ✅ Good: Null checks, readonly fields
public class CustomerService
{
    private readonly IRepository<Customer> _customerRepository;
    private readonly IMapper _mapper;
    private readonly ILogger<CustomerService> _logger;

    public CustomerService(
        IRepository<Customer> customerRepository,
        IMapper mapper,
        ILogger<CustomerService> logger)
    {
        _customerRepository = customerRepository ?? throw new ArgumentNullException(nameof(customerRepository));
        _mapper = mapper ?? throw new ArgumentNullException(nameof(mapper));
        _logger = logger ?? throw new ArgumentNullException(nameof(logger));
    }
}
```

## Error Handling & Logging

### Structured Logging

```csharp
// ❌ Bad: String concatenation, no context
_logger.LogInformation("Processing customer " + customerId);

// ✅ Good: Structured logging with context
_logger.LogInformation("Processing customer {CustomerId} for store {StoreId}", 
    customerId, storeId);

// ✅ Good: Different levels for different scenarios
_logger.LogDebug("Query executed in {ElapsedMs}ms", stopwatch.ElapsedMilliseconds);
_logger.LogWarning("No discounts found for {CustomerId}", customerId);
_logger.LogError(ex, "Failed to process rating for customer {CustomerId}", customerId);
```

### Exception Handling

```csharp
// Command Handler with proper exception handling
public async Task<int> Handle(CreateDiscountCommand request, CancellationToken cancellationToken)
{
    try
    {
        var customer = await _customerRepository.GetByIdAsync(request.CustomerId);
        if (customer == null)
            throw new CustomerNotFoundException($"Customer {request.CustomerId} not found");

        var discount = Discount.Create(request.CustomerId, request.Amount, request.FiscalYear);
        _discountRepository.Add(discount);
        await _discountRepository.SaveChangesAsync();

        return discount.Id;
    }
    catch (CustomerNotFoundException ex)
    {
        _logger.LogWarning(ex, "Invalid customer in discount creation");
        throw; // Let controller handle domain exceptions
    }
    catch (Exception ex)
    {
        _logger.LogError(ex, "Unexpected error creating discount");
        throw; // Wrap or translate if needed
    }
}
```

## Async/Await Patterns

### Async Methods

```csharp
// ✅ Good: Async throughout the stack
public async Task<CustomerDto> GetCustomerAsync(int id)
{
    var customer = await _customerRepository.GetByIdAsync(id);
    return _mapper.Map<CustomerDto>(customer);
}

// ✅ Good: ConfigureAwait(false) in libraries
public async Task<List<Customer>> GetAllAsync()
{
    return await _session.Query<Customer>()
        .ToListAsync()
        .ConfigureAwait(false);
}

// ❌ Bad: Blocking on async (deadlock risk)
var result = GetCustomerAsync(1).Result; // NEVER do this
```

## Testing Standards

### Unit Testing with xUnit + Moq

```csharp
public class CreateDiscountCommandHandlerTests
{
    private readonly Mock<IRepository<Discount>> _mockRepository;
    private readonly Mock<ILogger<CreateDiscountCommandHandler>> _mockLogger;
    private readonly CreateDiscountCommandHandler _handler;

    public CreateDiscountCommandHandlerTests()
    {
        _mockRepository = new Mock<IRepository<Discount>>();
        _mockLogger = new Mock<ILogger<CreateDiscountCommandHandler>>();
        _handler = new CreateDiscountCommandHandler(_mockRepository.Object, _mockLogger.Object);
    }

    [Fact]
    public async Task Handle_ValidCommand_CreatesDiscount()
    {
        // Arrange
        var command = new CreateDiscountCommand 
        { 
            CustomerId = 1, 
            Amount = 50, 
            FiscalYear = "2024" 
        };

        // Act
        var result = await _handler.Handle(command, CancellationToken.None);

        // Assert
        Assert.True(result > 0);
        _mockRepository.Verify(r => r.Add(It.IsAny<Discount>()), Times.Once);
    }

    [Fact]
    public async Task Handle_NullCommand_ThrowsArgumentNullException()
    {
        // Act & Assert
        await Assert.ThrowsAsync<ArgumentNullException>(() => _handler.Handle(null, CancellationToken.None));
    }
}
```

### Integration Testing with Real Database

```csharp
public class CustomerRepositoryIntegrationTests : IAsyncLifetime
{
    private ISessionFactory _sessionFactory;
    private ISession _session;

    public async Task InitializeAsync()
    {
        _sessionFactory = CreateTestSessionFactory();
        _session = _sessionFactory.OpenSession();
        await _session.CreateQuery("DELETE FROM Discounts").ExecuteUpdateAsync();
        await _session.CreateQuery("DELETE FROM Customers").ExecuteUpdateAsync();
    }

    public async Task DisposeAsync()
    {
        await _session.FlushAsync();
        _session?.Dispose();
        _sessionFactory?.Dispose();
    }

    [Fact]
    public async Task GetByIdAsync_ExistingCustomer_ReturnsCustomer()
    {
        // Arrange
        var customer = Customer.Create("123", "Smith", "John", CustomerStatus.Active, 2024);
        var repository = new Repository<Customer>(_session);
        repository.Add(customer);
        await repository.SaveChangesAsync();

        // Act
        var result = await repository.GetByIdAsync(customer.Id);

        // Assert
        Assert.NotNull(result);
        Assert.Equal("Smith", result.Surname);
    }
}
```

## Documentation & XML Comments

### Comprehensive XML Documentation

```csharp
/// <summary>
/// Places an order for a customer in the specified fiscal year.
/// </summary>
/// <param name="customerId">The unique identifier of the customer.</param>
/// <param name="fiscalYear">The fiscal year (e.g., "2024").</param>
/// <param name="categoryId">The ID of the category to order from.</param>
/// <returns>A task representing the order operation result.</returns>
/// <exception cref="CustomerNotFoundException">Thrown when the customer does not exist.</exception>
/// <exception cref="InvalidOperationException">Thrown when the customer is not eligible to place an order.</exception>
/// <remarks>
/// Only customers with status "Active" can place orders during the allowed time windows.
/// </remarks>
public async Task PlaceOrderAsync(int customerId, string fiscalYear, int categoryId)
{
    // Implementation
}
```

## Code Quality & Review Standards

### SonarQube / Code Analysis

- Maintain minimum 80% test coverage
- Zero critical/high security issues
- Code smells: Keep methods under 30 lines
- Cognitive complexity: Keep under 15
- Duplicated code: Keep DRY principle

### PR Review Checklist

- [ ] Code follows SOLID principles
- [ ] NHibernate queries optimized (no N+1)
- [ ] Error handling with proper logging
- [ ] Async/await used correctly
- [ ] Null checks and argument validation
- [ ] Unit tests included (80%+ coverage)
- [ ] XML documentation on public APIs
- [ ] No magic strings (use constants, config)
- [ ] Security: No SQL injection, proper auth checks
- [ ] Authorization/context filtering applied

## Related Skills

- See **backend-architecture** for overall system design
- See **nhibernate** for ORM-specific patterns
- See **testing-guidelines** for test structure and utilities
- See **sql-code-review** for database code quality

---

**Summary**: Follow SOLID principles, use Clean Architecture layers, leverage CQRS with MediatR for clear separation, properly implement NHibernate repositories, inject dependencies explicitly, log structurally, handle errors gracefully, apply authorization context filtering, and maintain comprehensive test coverage.
