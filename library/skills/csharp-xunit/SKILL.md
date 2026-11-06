---
name: csharp-xunit
description: xUnit best practices for .NET projects including test structure, data-driven tests, mocking patterns, and testing CQRS handlers and repositories
user-invokable: true
---

# xUnit Best Practices

Guide for writing effective unit tests with xUnit in .NET projects. Covers test structure, data-driven testing, mocking with Moq, and testing CQRS handlers, repositories, and domain models.

## Project Setup

### Test Project Structure
```
<Solution>.API.Tests/                        ← Main API tests
├── Handlers/
│   ├── Commands/
│   │   ├── CreateDiscountCommandHandlerTests.cs
│   │   └── UpdateCustomerRatingCommandHandlerTests.cs
│   └── Queries/
│       └── GetCustomersByStoreQueryHandlerTests.cs
├── Repositories/
│   ├── CustomerRepositoryTests.cs
│   └── DiscountRepositoryTests.cs
├── Domain/
│   └── CustomerTests.cs
├── Fixtures/
│   ├── CustomerFixture.cs
│   └── DiscountFixture.cs
└── TestData/
    └── TestDataBuilder.cs

<Solution>.Application.Tests/                  ← Application layer tests
<Solution>.Domain.Tests/                       ← Domain model tests
<Solution>.Infrastructure.Tests/                ← Infrastructure/Repository tests
```

### Dependencies in .csproj
```xml
<ItemGroup>
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.5.0" />
    <PackageReference Include="xunit" Version="2.6.0" />
    <PackageReference Include="xunit.runner.visualstudio" Version="2.4.5" />
    <PackageReference Include="Moq" Version="4.18.0" />
    <PackageReference Include="AutoFixture" Version="4.18.0" />
    <PackageReference Include="AutoFixture.Xunit2" Version="4.18.0" />
    <PackageReference Include="FluentAssertions" Version="6.12.0" />
</ItemGroup>
```

### Build & Run Tests
```bash
dotnet test                                 # Run all tests
dotnet test --filter=Category=Integration  # Run by trait
dotnet test --collect:"XPlat Code Coverage" # With coverage
```

## Test Structure (AAA Pattern)

### Arrange-Act-Assert Pattern

```csharp
[Fact]
public async Task CreateDiscountCommandHandler_ValidInput_CreatesDiscount()
{
    // Arrange: Set up test data and mocks
    var mockRepository = new Mock<IRepository<Discount>>();
    var mockLogger = new Mock<ILogger<CreateDiscountCommandHandler>>();
    var handler = new CreateDiscountCommandHandler(mockRepository.Object, mockLogger.Object);
    
    var command = new CreateDiscountCommand 
    { 
        CustomerId = 1, 
        Amount = 50, 
        FiscalYear = "2024" 
    };

    // Act: Execute the code being tested
    var result = await handler.Handle(command, CancellationToken.None);

    // Assert: Verify results
    Assert.True(result > 0);
    mockRepository.Verify(r => r.Add(It.IsAny<Discount>()), Times.Once);
    mockRepository.Verify(r => r.SaveChangesAsync(), Times.Once);
}
```

### Constructor for Setup, Dispose for Teardown

```csharp
public class CustomerRepositoryTests : IAsyncLifetime
{
    private ISessionFactory _sessionFactory;
    private ISession _session;
    private CustomerRepository _repository;

    // Setup runs before each test
    public async Task InitializeAsync()
    {
        _sessionFactory = CreateTestSessionFactory();
        _session = _sessionFactory.OpenSession();
        _repository = new CustomerRepository(_session);
        
        // Clean up test data
        await _session.CreateQuery("DELETE FROM Discounts").ExecuteUpdateAsync();
        await _session.CreateQuery("DELETE FROM Customers").ExecuteUpdateAsync();
    }

    // Teardown runs after each test
    public async Task DisposeAsync()
    {
        await _session.FlushAsync();
        _session?.Dispose();
        _sessionFactory?.Dispose();
    }

    [Fact]
    public async Task GetByIdAsync_ExistingCustomer_ReturnsCustomer()
    {
        // Use _repository created in InitializeAsync
    }
}
```

## Test Naming Convention

Follow: `MethodName_Scenario_ExpectedBehavior`

```csharp
// ✅ Good: Clear intent
[Fact]
public async Task CreateDiscountCommandHandler_ValidCommand_CreatesDiscountAndReturnsId()
{
}

// ✅ Good: Shows scenario
[Fact]
public async Task UpdateCustomerRatingCommandHandler_OutsideChangeWindow_ThrowsInvalidOperationException()
{
}

// ❌ Bad: Vague name
[Fact]
public async Task TestCreate()
{
}

// ❌ Bad: Too long and redundant
[Fact]
public async Task CreateDiscountCommandHandlerCanCreateADiscountWhenValidCommandIsPassedInAndExpectItToReturnTheIdOfTheNewlyCreatedDiscount()
{
}
```

## Standard Tests

### Unit Testing with Moq

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
        var command = new CreateDiscountCommand { CustomerId = 1, Amount = 50, FiscalYear = "2024" };

        // Act
        var result = await _handler.Handle(command, CancellationToken.None);

        // Assert
        Assert.True(result > 0);
        _mockRepository.Verify(r => r.Add(It.IsAny<Discount>()), Times.Once);
        _mockRepository.Verify(r => r.SaveChangesAsync(), Times.Once);
    }

    [Fact]
    public async Task Handle_NullCommand_ThrowsArgumentNullException()
    {
        // Act & Assert
        await Assert.ThrowsAsync<ArgumentNullException>(() => _handler.Handle(null, CancellationToken.None));
    }

    [Fact]
    public async Task Handle_InvalidCustomerId_ThrowsCustomerNotFoundException()
    {
        // Arrange
        var command = new CreateDiscountCommand { CustomerId = 999, Amount = 50, FiscalYear = "2024" };
        _mockRepository.Setup(r => r.GetByIdAsync(999)).ReturnsAsync((Discount)null);

        // Act & Assert
        await Assert.ThrowsAsync<CustomerNotFoundException>(() => _handler.Handle(command, CancellationToken.None));
    }
}
```

## Data-Driven Tests

### [InlineData] for Simple Test Cases

```csharp
public class CustomerStatusValidationTests
{
    [Theory]
    [InlineData("Active", true)]
    [InlineData("Inactive", false)]
    [InlineData("Archived", false)]
    public void IsEligibleForOrder_ByStatus_ReturnsExpected(string status, bool expected)
    {
        // Arrange
        var customer = Customer.Create("123", "Smith", "John", (CustomerStatus)Enum.Parse(typeof(CustomerStatus), status), 2024);

        // Act
        var result = customer.IsEligibleForOrder();

        // Assert
        Assert.Equal(expected, result);
    }
}
```

### [MemberData] for Complex Test Data

```csharp
public class RatingCalculationTests
{
    public static IEnumerable<object[]> InvalidRatingValues =>
        new List<object[]>
        {
            new object[] { 0m, "Rating cannot be below 1" },
            new object[] { 5.5m, "Rating cannot exceed 5" },
            new object[] { 0.5m, "Rating cannot be below 1" }
        };

    [Theory]
    [MemberData(nameof(InvalidRatingValues))]
    public void SetRating_InvalidValues_ThrowsArgumentException(decimal rating, string expectedMessage)
    {
        // Arrange
        var order = Order.Create(1, 1, 2024);

        // Act & Assert
        var ex = Assert.Throws<ArgumentException>(() => order.SetRating(rating));
        Assert.Contains(expectedMessage, ex.Message);
    }
}
```

### AutoFixture with xUnit for AutoData

```csharp
public class CustomerOrderHandlerTests
{
    [Theory]
    [AutoData]  // AutoFixture generates random valid data automatically
    public async Task Handle_ValidCommand_PlacesOrder(
        int customerId, 
        string fiscalYear,
        CreateDiscountCommand command)
    {
        // Arrange: Use auto-generated data
        // Act & Assert
    }

    [Theory]
    [InlineAutoData("2024")]
    [InlineAutoData("2023")]
    public async Task Handle_VariousYears_Succeeds(string fiscalYear, CreateDiscountCommand command)
    {
        // Arrange: fiscalYear from InlineAutoData, rest auto-generated
    }
}
```

## Assertions with FluentAssertions

### Standard Assertions

```csharp
[Fact]
public async Task GetCustomersByStoreQuery_ReturnsCorrectCount()
{
    // Arrange & Act
    var result = await handler.Handle(query, CancellationToken.None);

    // Assert - Traditional
    Assert.NotNull(result);
    Assert.Equal(5, result.Count);
    Assert.All(result, c => Assert.NotEmpty(c.Surname));
}

// Assert - Fluent (more readable)
result.Should().NotBeNull();
result.Should().HaveCount(5);
result.Should().AllSatisfy(c => c.Surname.Should().NotBeEmpty());
```

### Exception Assertions

```csharp
[Fact]
public async Task Handle_OutsideChangeWindow_ThrowsInvalidOperationException()
{
    // Arrange
    var command = new UpdateCustomerRatingCommand { CustomerId = 1, Rating = 4 };

    // Act & Assert - Traditional
    await Assert.ThrowsAsync<InvalidOperationException>(() => handler.Handle(command, CancellationToken.None));

    // Assert - Fluent
    await handler.Invoking(h => h.Handle(command, CancellationToken.None))
        .Should()
        .ThrowAsync<InvalidOperationException>()
        .WithMessage("*not allowed outside time window*");
}
```

### Collection Assertions

```csharp
[Fact]
public void GetCustomers_ByStore_ReturnsFiltered()
{
    // Arrange & Act
    var result = repository.GetAll()
        .Where(c => c.StoreId == 1)
        .ToList();

    // Assert
    result.Should()
        .NotBeEmpty()
        .And.AllSatisfy(c => c.StoreId.Should().Be(1))
        .And.BeInAscendingOrder(c => c.Surname);
}
```

## Testing CQRS Handlers & Repositories

### Command Handler Testing

```csharp
[Fact]
public async Task CreateDiscountCommandHandler_ValidInput_CallsRepositoryAndSaves()
{
    // Arrange
    var mockRepository = new Mock<IRepository<Discount>>();
    mockRepository.Setup(r => r.SaveChangesAsync())
        .ReturnsAsync(1)
        .Verifiable();
    
    var handler = new CreateDiscountCommandHandler(mockRepository.Object, Mock.Of<ILogger<CreateDiscountCommandHandler>>());
    var command = new CreateDiscountCommand { CustomerId = 1, Amount = 50, FiscalYear = "2024" };

    // Act
    var result = await handler.Handle(command, CancellationToken.None);

    // Assert
    result.Should().BeGreaterThan(0);
    mockRepository.Verify();  // Verify all Setup.Verifiable() calls were made
}
```

### Query Handler Testing (Read-Only)

```csharp
[Fact]
public async Task GetCustomersByStoreQueryHandler_ReturnsProjectedDtos()
{
    // Arrange
    var mockRepository = new Mock<IRepository<Customer>>();
    mockRepository.Setup(r => r.GetAll())
        .Returns(new List<Customer> 
        { 
            Customer.Create("123", "Smith", "John", CustomerStatus.Active, 2024)
        }.AsQueryable());

    var handler = new GetCustomersByStoreQueryHandler(mockRepository.Object);
    var query = new GetCustomersByStoreQuery { StoreId = 1 };

    // Act
    var result = await handler.Handle(query, CancellationToken.None);

    // Assert
    result.Should()
        .HaveCount(1)
        .And.Contain(c => c.Surname == "Smith");
}
```

### Repository Testing with Real Database

```csharp
[Collection("Database collection")]
public class CustomerRepositoryIntegrationTests : IAsyncLifetime
{
    private ISessionFactory _sessionFactory;
    private ISession _session;
    private CustomerRepository _repository;

    public async Task InitializeAsync()
    {
        _sessionFactory = CreateInMemorySessionFactory();
        _session = _sessionFactory.OpenSession();
        _repository = new CustomerRepository(_session);
        await CleanupDatabase();
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
        var customer = Customer.Create("223", "Smith", "Jane", CustomerStatus.Active, 2024);
        _repository.Add(customer);
        await _repository.SaveChangesAsync();

        // Act
        var result = await _repository.GetByIdAsync(customer.Id);

        // Assert
        result.Should().NotBeNull();
        result.Surname.Should().Be("Smith");
    }

    [Fact]
    public async Task GetByIdAsync_NonExistent_ReturnsNull()
    {
        // Act
        var result = await _repository.GetByIdAsync(999);

        // Assert
        result.Should().BeNull();
    }
}
```

### Domain Model Testing

```csharp
public class CustomerTests
{
    [Fact]
    public void Create_ValidData_CreatesCustomer()
    {
        // Act
        var customer = Customer.Create("123", "Smith", "John", CustomerStatus.Active, 2024);

        // Assert
        customer.Should().NotBeNull();
        customer.CustomerNumber.Should().Be("123");
        customer.Surname.Should().Be("Smith");
        customer.Status.Should().Be(CustomerStatus.Active);
    }

    [Fact]
    public void PlaceOrder_ActiveCustomer_Succeeds()
    {
        // Arrange
        var customer = Customer.Create("123", "Smith", "John", CustomerStatus.Active, 2024);

        // Act
        customer.PlaceOrder("2024");

        // Assert
        customer.Status.Should().Be(CustomerStatus.Placed);
        customer.LastOrderDate.Should().BeCloseTo(DateTime.Now, TimeSpan.FromSeconds(1));
    }

    [Fact]
    public void PlaceOrder_InactiveCustomer_ThrowsInvalidOperationException()
    {
        // Arrange
        var customer = Customer.Create("123", "Smith", "John", CustomerStatus.Inactive, 2024);

        // Act & Assert
        customer.Invoking(c => c.PlaceOrder("2024"))
            .Should()
            .Throw<InvalidOperationException>()
            .WithMessage("*Cannot place order for inactive customer*");
    }
}
```

## Test Organization & Traits

### Using Traits for Test Categorization

```csharp
[Collection("Database collection")]
public class CustomerRepositoryIntegrationTests
{
    [Fact(DisplayName = "Repository should retrieve customer by ID")]
    [Trait("Category", "Integration")]
    [Trait("Type", "Database")]
    public async Task GetByIdAsync_ExistingCustomer_ReturnsCustomer()
    {
    }

    [Fact]
    [Trait("Category", "Unit")]
    [Trait("Priority", "High")]
    public async Task CreateDiscountCommandHandler_ValidInput_CreatesDiscount()
    {
    }
}

// Run by trait: dotnet test --filter="Category=Integration"
// Run by priority: dotnet test --filter="Priority=High"
```

### Test Fixtures for Shared State

```csharp
// Fixture for all Discount tests
public class DiscountTestFixture : IAsyncLifetime
{
    private ISessionFactory _sessionFactory;
    public ISession Session { get; private set; }
    public DiscountRepository Repository { get; private set; }

    public async Task InitializeAsync()
    {
        _sessionFactory = CreateTestSessionFactory();
        Session = _sessionFactory.OpenSession();
        Repository = new DiscountRepository(Session);
        await CleanupDatabase();
    }

    public async Task DisposeAsync()
    {
        await Session.FlushAsync();
        Session?.Dispose();
        _sessionFactory?.Dispose();
    }

    private async Task CleanupDatabase()
    {
        await Session.CreateQuery("DELETE FROM Discounts").ExecuteUpdateAsync();
    }
}

// Use in tests
public class DiscountRepositoryTests : IClassFixture<DiscountTestFixture>
{
    private readonly DiscountTestFixture _fixture;

    public DiscountRepositoryTests(DiscountTestFixture fixture)
    {
        _fixture = fixture;
    }

    [Fact]
    public async Task GetByIdAsync_ExistingDiscount_ReturnsDiscount()
    {
        // Use _fixture.Repository
    }
}
```

## Test Coverage Goals

- **Overall**: 80%+ coverage
- **Domain Models**: 100% (business logic is critical)
- **Command Handlers**: 85%+ (happy path + major failure scenarios)
- **Query Handlers**: 75%+ (focus on correctness, not edge cases)
- **Repositories**: Integration tests only (mock queries are not valuable)
- **Infrastructure**: 60%+ (logging, configuration)

## Best Practices Checklist

- [ ] Test class has clear, focused responsibility (tests one handler/repository/entity)
- [ ] Test name follows `MethodName_Scenario_ExpectedBehavior` pattern
- [ ] AAA pattern (Arrange, Act, Assert) is followed
- [ ] No test interdependencies (tests can run in any order)
- [ ] Mocks/stubs created fresh in each test
- [ ] Assertions are clear and specific
- [ ] No `Thread.Sleep()` or arbitrary waits
- [ ] Async methods tested with proper `await`
- [ ] Edge cases and error paths tested
- [ ] No hardcoded values (use meaningful constants)
- [ ] Test data is minimal and clear
- [ ] Disposable resources cleaned up in `DisposeAsync()`
- [ ] Tests marked with appropriate traits for categorization

## Related Skills

- See **testing-guidelines** for ngxtest (Jasmine) patterns and overall testing architecture
- See **dotnet-best-practices** for implementation patterns being tested
- See **nhibernate** for repository patterns
- See **dotnet-design-pattern-review** for CQRS handler design validation

---

**Summary**: Follow AAA pattern, use clear test names, leverage AutoFixture and Moq for isolation, implement proper fixtures for setup/teardown, test domain models thoroughly, separate unit from integration tests, use traits for organization, and validate domain-specific concerns like authorization and time-window rules.
