---
name: csharp-async
description: C# async/await best practices for .NET backends (Task-based asynchronous pattern, ConfigureAwait, cancellation tokens, NHibernate async queries)
user-invokable: true
---

# C# Async/Await Best Practices

Guide for writing effective asynchronous C# code in .NET backends. Covers async/await patterns, proper Task usage, ConfigureAwait handling, cancellation tokens, and async patterns for MediatR handlers, NHibernate repositories, and HTTP operations.

## Naming Conventions

### Async Method Naming

All async methods must have `Async` suffix:

```csharp
// ✅ Good: Clear async signature
public async Task<Customer> GetCustomerByIdAsync(int customerId, CancellationToken cancellationToken = default)
{
    return await _session.GetAsync<Customer>(customerId, cancellation: cancellationToken);
}

// ❌ Bad: Missing Async suffix
public async Task<Customer> GetCustomerById(int customerId)
{
}

// ✅ Good: Void Async only for event handlers
public async void OnButtonClickAsync(object sender, EventArgs e)
{
    await ProcessAsync();
}
```

### Match Sync/Async Pairs

When providing both sync and async versions:

```csharp
// Sync version (rarely used, mostly async)
public Customer GetCustomerById(int customerId)
{
    return _session.Get<Customer>(customerId);
}

// Async version (preferred)
public async Task<Customer> GetCustomerByIdAsync(int customerId, CancellationToken cancellationToken = default)
{
    return await _session.GetAsync<Customer>(customerId, cancellation: cancellationToken);
}
```

## Return Types

### Task vs Task<T>

```csharp
// ✅ Good: Returns value
public async Task<Discount> CreateDiscountAsync(CreateDiscountCommand command, CancellationToken cancellationToken)
{
    var discount = Discount.Create(command.CustomerId, command.Amount);
    _repository.Add(discount);
    await _repository.SaveChangesAsync(cancellationToken);
    return discount;
}

// ✅ Good: No return value
public async Task DeleteCustomerAsync(int customerId, CancellationToken cancellationToken)
{
    var customer = await _repository.GetByIdAsync(customerId, cancellationToken);
    _repository.Delete(customer);
    await _repository.SaveChangesAsync(cancellationToken);
}

// ❌ Bad: Async void (except event handlers) causes fire-and-forget
public async void ProcessDiscountAsync(int discountId)
{
    await _discountService.ProcessAsync(discountId);
}
```

### ValueTask<T> for High-Performance Scenarios

Use `ValueTask<T>` when method frequently completes synchronously:

```csharp
// ✅ Good: Cache hit is common, allocation saved
public async ValueTask<Customer> GetCustomerFromCacheAsync(int customerId)
{
    if (_cache.TryGetValue(customerId, out var customer))
    {
        return customer; // Doesn't allocate Task if cached
    }
    return await _repository.GetByIdAsync(customerId);
}

// ❌ Bad: Use Task for I/O-bound operations
public async ValueTask<List<Customer>> GetCustomersByStoreAsync(int storeId)
{
    return await _repository.GetByStoreAsync(storeId);
}
```

## ConfigureAwait Usage

### Library Code (<Solution>.Application, Infrastructure, Domain)

Always use `ConfigureAwait(false)` in library code to prevent UI thread deadlocks and improve performance:

```csharp
// ✅ Good: Library code can be called from any context
public async Task<Customer> GetCustomerAsync(int customerId)
{
    var order = await _session.GetAsync<Order>(customerId)
        .ConfigureAwait(false);
    
    var ratingHistory = await _ratingRepository.GetHistoryAsync(customerId)
        .ConfigureAwait(false);
    
    return new CustomerDto { ... };
}

// ❌ Bad: Missing ConfigureAwait in library
public async Task<Discount> CreateDiscountAsync(CreateDiscountCommand command)
{
    var customer = await _repository.GetByIdAsync(command.CustomerId);
    // Potential deadlock if called from UI context
}
```

### API Endpoints (Controller layer)

Use `ConfigureAwait(true)` or omit (defaults to true) because you control the context:

```csharp
[HttpGet("{id}")]
public async Task<ActionResult<CustomerDto>> GetCustomer(int id, CancellationToken cancellationToken)
{
    // ConfigureAwait(true) is fine - we're in API context
    var customer = await _queryHandler.Handle(new GetCustomerQuery { Id = id }, cancellationToken);
    return Ok(customer);
}
```

## Exception Handling

### Try/Catch with Async

```csharp
// ✅ Good: Catch exceptions from async operation
public async Task<Discount> CreateDiscountAsync(CreateDiscountCommand command, CancellationToken cancellationToken)
{
    try
    {
        var discount = Discount.Create(command.CustomerId, command.Amount);
        _repository.Add(discount);
        await _repository.SaveChangesAsync(cancellationToken)
            .ConfigureAwait(false);
        return discount;
    }
    catch (DbUpdateException ex)
    {
        _logger.LogError(ex, "Failed to create discount for customer {CustomerId}", command.CustomerId);
        throw new DiscountCreationFailedException("Database error", ex);
    }
    catch (OperationCanceledException ex)
    {
        _logger.LogWarning("Discount creation cancelled");
        throw;
    }
}

// ❌ Bad: Swallowing exceptions
public async Task<Discount> CreateDiscountAsync(CreateDiscountCommand command, CancellationToken cancellationToken)
{
    try
    {
        return await _discountService.CreateAsync(command, cancellationToken);
    }
    catch
    {
        // Exception lost!
        return null;
    }
}
```

### Using Task.FromException for Sync Errors in Async Methods

```csharp
// ✅ Good: Return faulted Task instead of throwing
public async Task<Customer> GetCustomerAsync(int customerId, CancellationToken cancellationToken)
{
    if (customerId <= 0)
    {
        return await Task.FromException<Customer>(
            new ArgumentException("Customer ID must be positive", nameof(customerId))
        ).ConfigureAwait(false);
    }
    return await _repository.GetByIdAsync(customerId, cancellationToken)
        .ConfigureAwait(false);
}

// ❌ Bad: Throwing in async method (can cause fire-and-forget issues)
public async Task<Customer> GetCustomerAsync(int customerId)
{
    if (customerId <= 0) throw new ArgumentException("...");
    return await _repository.GetByIdAsync(customerId);
}
```

## Cancellation Tokens

### Propagate CancellationToken Throughout Call Chain

```csharp
// API Endpoint
[HttpPost]
public async Task<ActionResult> CreateDiscount(
    CreateDiscountCommand command,
    CancellationToken cancellationToken)  // ✅ Provided by ASP.NET Core
{
    var result = await _handler.Handle(command, cancellationToken);
    return Ok(result);
}

// Command Handler (MediatR)
public class CreateDiscountCommandHandler : IRequestHandler<CreateDiscountCommand, int>
{
    public async Task<int> Handle(CreateDiscountCommand request, CancellationToken cancellationToken)
    {
        // ✅ Pass cancellationToken to repository
        var customer = await _customerRepository.GetByIdAsync(request.CustomerId, cancellationToken)
            .ConfigureAwait(false);
        
        var discount = Discount.Create(customer.Id, request.Amount);
        _discountRepository.Add(discount);
        
        // ✅ Pass to async database operation
        await _unitOfWork.SaveChangesAsync(cancellationToken)
            .ConfigureAwait(false);
        
        return discount.Id;
    }
}

// Repository (Infrastructure)
public async Task<Customer> GetByIdAsync(int customerId, CancellationToken cancellationToken = default)
{
    return await _session.GetAsync<Customer>(customerId, cancellation: cancellationToken)
        .ConfigureAwait(false);
}

// ❌ Bad: Ignoring cancellation token
public async Task<Customer> GetByIdAsync(int customerId, CancellationToken cancellationToken)
{
    // Token not used - can't cancel long-running query
    return await _session.GetAsync<Customer>(customerId);
}
```

### Default Parameters for CancellationToken

```csharp
// ✅ Good: Allows calling without token
public async Task<List<Customer>> GetAllAsync(CancellationToken cancellationToken = default)
{
    return await _repository.GetAllAsync(cancellationToken)
        .ConfigureAwait(false);
}

// Usage
var customers = await _repository.GetAllAsync();  // Works - uses default (non-cancellable)
var customers = await _repository.GetAllAsync(ct);  // Works - respects cancellation
```

## NHibernate Async Queries

### Async Query Execution

```csharp
// ✅ Good: Async query execution
public async Task<List<Customer>> GetByStoreAsync(int storeId, CancellationToken cancellationToken)
{
    return await _session.Query<Customer>()
        .Where(c => c.StoreId == storeId)
        .ToListAsync(cancellationToken)
        .ConfigureAwait(false);
}

// ✅ Good: Async with eager loading
public async Task<Customer> GetWithOrdersAsync(int customerId, CancellationToken cancellationToken)
{
    return await _session.Query<Customer>()
        .Fetch(c => c.Orders)
        .Where(c => c.Id == customerId)
        .FirstOrDefaultAsync(cancellationToken)
        .ConfigureAwait(false);
}

// ✅ Good: Async GetAsync for single entity
public async Task<Customer> GetByIdAsync(int customerId, CancellationToken cancellationToken)
{
    return await _session.GetAsync<Customer>(customerId, cancellation: cancellationToken)
        .ConfigureAwait(false);
}

// ❌ Bad: Sync-over-async (blocks thread)
public async Task<List<Customer>> GetByStoreAsync(int storeId)
{
    // Blocks thread waiting for result
    return _session.Query<Customer>()
        .Where(c => c.StoreId == storeId)
        .ToList();
}

// ❌ Bad: Mixing blocking and async
public async Task<Customer> GetWithOrdersAsync(int customerId)
{
    var customer = await _repository.GetByIdAsync(customerId);
    customer.Orders.ToList();  // Forces sync load
    return customer;
}
```

## Parallel Async Operations

### Task.WhenAll for Multiple Independent Operations

```csharp
// ✅ Good: Execute multiple operations in parallel
public async Task<OrderDetailDto> GetOrderDetailsAsync(int orderId, CancellationToken cancellationToken)
{
    var order = _session.GetAsync<Order>(orderId, cancellation: cancellationToken);
    var ratingHistory = _ratingRepository.GetHistoryAsync(orderId, cancellationToken);
    var shipments = _shipmentRepository.GetShipmentsByOrderAsync(orderId, cancellationToken);

    // Execute all 3 in parallel
    await Task.WhenAll(order, ratingHistory, shipments)
        .ConfigureAwait(false);

    return new OrderDetailDto
    {
        Order = await order.ConfigureAwait(false),
        RatingHistory = await ratingHistory.ConfigureAwait(false),
        Shipments = await shipments.ConfigureAwait(false)
    };
}

// ✅ Good: With proper error handling
public async Task<(Customer, List<Discount>, List<Order>)> GetCustomerDataAsync(
    int customerId,
    CancellationToken cancellationToken)
{
    var customerTask = _customerRepository.GetByIdAsync(customerId, cancellationToken);
    var discountsTask = _discountRepository.GetByCustomerAsync(customerId, cancellationToken);
    var ordersTask = _orderRepository.GetByCustomerAsync(customerId, cancellationToken);

    try
    {
        await Task.WhenAll(customerTask, discountsTask, ordersTask)
            .ConfigureAwait(false);

        return (
            await customerTask.ConfigureAwait(false),
            await discountsTask.ConfigureAwait(false),
            await ordersTask.ConfigureAwait(false)
        );
    }
    catch (Exception ex)
    {
        _logger.LogError(ex, "Failed to load customer data for {CustomerId}", customerId);
        throw;
    }
}

// ❌ Bad: Sequential instead of parallel
public async Task<OrderDetailDto> GetOrderDetailsAsync(int orderId, CancellationToken cancellationToken)
{
    var order = await _repository.GetByIdAsync(orderId, cancellationToken);
    var ratingHistory = await _ratingRepository.GetHistoryAsync(orderId, cancellationToken);
    var shipments = await _shipmentRepository.GetShipmentsAsync(orderId, cancellationToken);
    // All waited sequentially instead of in parallel
}
```

### Task.WhenAny for First Completed or Timeout

```csharp
// ✅ Good: Timeout on long-running query
public async Task<List<Customer>> GetCustomersWithTimeoutAsync(int storeId, CancellationToken cancellationToken)
{
    using var cts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
    cts.CancelAfter(TimeSpan.FromSeconds(30));  // 30-second timeout

    try
    {
        return await _repository.GetByStoreAsync(storeId, cts.Token)
            .ConfigureAwait(false);
    }
    catch (OperationCanceledException)
    {
        throw new TimeoutException("Customer query exceeded 30 seconds");
    }
}

// ✅ Good: Multiple sources, take first
public async Task<Store> GetNearestStoreAsync(double latitude, double longitude, CancellationToken cancellationToken)
{
    var localSource = _localRepository.GetNearestAsync(latitude, longitude, cancellationToken);
    var remoteSource = _remoteService.GetNearestAsync(latitude, longitude, cancellationToken);

    var completed = await Task.WhenAny(localSource, remoteSource)
        .ConfigureAwait(false);

    return completed == localSource
        ? await localSource.ConfigureAwait(false)
        : await remoteSource.ConfigureAwait(false);
}
```

## Common Pitfalls

### Never Use .Wait(), .Result, or .GetAwaiter().GetResult()

```csharp
// ❌ CRITICAL ERROR: Deadlock risk
public class CustomerService
{
    public Customer GetCustomer(int id)
    {
        // BLOCKS THREAD - can deadlock in UI contexts
        return _repository.GetByIdAsync(id).Result;
    }
}

// ❌ CRITICAL ERROR: Same as .Result
public class CustomerController
{
    public CustomerDto GetCustomer(int id)
    {
        var task = _repository.GetByIdAsync(id);
        task.Wait();  // Blocks - defeats purpose of async
        return task.Result;
    }
}

// ❌ CRITICAL ERROR: Hides async with GetAwaiter()
public class CustomerBuilder
{
    private Customer _customer = MyAsyncMethod().GetAwaiter().GetResult();
}

// ✅ CORRECT: Make caller async
public async Task<Customer> GetCustomer(int id)
{
    return await _repository.GetByIdAsync(id)
        .ConfigureAwait(false);
}
```

### Avoid Async Void (Except Event Handlers)

```csharp
// ❌ BAD: Fire-and-forget, exceptions lost
public async void ProcessDiscountAsync(int discountId)
{
    await _discountService.ProcessAsync(discountId);  // Exception swallowed
}

// ✅ GOOD: Return Task for proper error handling
public async Task ProcessDiscountAsync(int discountId)
{
    await _discountService.ProcessAsync(discountId);
}

// ✅ ACCEPTABLE: Event handler (special case)
private async void OnDiscountProcessed(object sender, EventArgs e)
{
    await HandleDiscountAsync();
}
```

### Avoid Unnecessary Async/Await

```csharp
// ❌ Bad: Unnecessary allocation for synchronous pass-through
public async Task<Customer> GetCustomerAsync(int id)
{
    return await _repository.GetByIdAsync(id);  // Just return the Task
}

// ✅ Good: Return Task directly
public Task<Customer> GetCustomerAsync(int id)
{
    return _repository.GetByIdAsync(id);
}

// ❌ Bad: Awaiting when not needed
public async Task ProcessCustomersAsync(List<int> customerIds)
{
    var tasks = customerIds.Select(id => _repository.GetByIdAsync(id));
    var customers = await Task.WhenAll(tasks);  // Unnecessary async wrapper
    return customers;
}

// ✅ Good: Return Task directly
public Task<Customer[]> GetCustomersAsync(List<int> customerIds)
{
    var tasks = customerIds.Select(id => _repository.GetByIdAsync(id));
    return Task.WhenAll(tasks);
}
```

## Batch Processing with Async Parallelization

```csharp
// ✅ Good: Process customers in batches with parallelism
public async Task ProcessCustomerDiscountsAsync(List<int> customerIds, CancellationToken cancellationToken)
{
    const int batchSize = 10;
    
    for (int i = 0; i < customerIds.Count; i += batchSize)
    {
        var batch = customerIds.Skip(i).Take(batchSize);
        
        var tasks = batch.Select(id => ProcessCustomerAsync(id, cancellationToken));
        
        await Task.WhenAll(tasks)
            .ConfigureAwait(false);
    }
}

// ✅ Good: Use Parallel.ForEachAsync for concurrent processing (async version of Parallel.ForEach)
public async Task ProcessCustomersInParallelAsync(List<Customer> customers, CancellationToken cancellationToken)
{
    await Parallel.ForEachAsync(
        customers,
        new ParallelOptions { CancellationToken = cancellationToken, MaxDegreeOfParallelism = Environment.ProcessorCount },
        async (customer, ct) =>
        {
            await _discountService.ApplyDiscountAsync(customer.Id, ct)
                .ConfigureAwait(false);
        }
    ).ConfigureAwait(false);
}
```

## Best Practices Checklist

- [ ] All async methods end with `Async` suffix
- [ ] Return `Task<T>` or `Task`, not `Task<void>`
- [ ] `ConfigureAwait(false)` used in library code
- [ ] `CancellationToken` propagated through call chain
- [ ] Exception handling around await expressions
- [ ] Never use `.Result`, `.Wait()`, or `.GetAwaiter().GetResult()`
- [ ] No async void methods (except event handlers)
- [ ] Multiple independent operations use `Task.WhenAll()`
- [ ] Cancellation tokens default to `default` parameter
- [ ] Long-running operations have timeout support
- [ ] No unnecessary async/await wrapping
- [ ] Proper logging of cancellation scenarios
- [ ] NHibernate async methods used (GetAsync, ToListAsync)

## Related Skills

- See **dotnet-best-practices** for CQRS handler implementation patterns
- See **nhibernate** for async query patterns with NHibernate
- See **csharp-xunit** for testing async code
- See **backend-architecture** for overall async architecture in Clean Architecture

---

**Summary**: Use `async/await` for all I/O operations, propagate `CancellationToken` throughout call chains, use `ConfigureAwait(false)` in libraries, avoid `.Result` and `.Wait()` completely, never use async void (except handlers), and leverage `Task.WhenAll()` for parallelism. Also covered: batch processing with bounded parallelism.
