---
name: backend-architecture
description: .NET Core backend architecture - Clean Architecture layers, NHibernate ORM, CQRS with MediatR, Repository pattern, AutoMapper, security filtering
user-invokable: true
---

# Backend Architecture (.NET Core)

## Clean Architecture Layers

The backend follows **Clean Architecture** principles with clear separation of concerns:

```
<Solution>.API/              # Presentation Layer (Controllers, DTOs, Middleware)
<Solution>.Application/      # Application Layer (CQRS: Commands/Queries/Handlers)
<Solution>.Domain/           # Domain Layer (Entities, Domain Models, Interfaces)
<Solution>.Infrastructure/   # Infrastructure Layer (Repositories, NHibernate Mappings, DB Access)
<Solution>.Service/          # Service Layer (Business Logic, Helpers, DTOs, Validators)
<Solution>.ActivityLogger/   # Cross-cutting Concern (Logging)
<Solution>.Mail/             # Cross-cutting Concern (Email)
<Solution>.Migration/        # Database Migrations (FluentMigrator)
```

### Layer Responsibilities

#### <Solution>.API (Presentation)
- **Controllers**: REST API endpoints
- **MapperProfiles**: AutoMapper profiles for Entity → API Model mapping
- **Middleware**: Authentication, Authorization, Exception handling
- **Startup.cs**: Dependency Injection configuration, pipeline setup
- **Program.cs**: Application entry point

**Key Patterns**:
- Controllers return API-specific models (not DTOs or entities)
- AutoMapper for clean separation between layers
- OpenAPI/Swagger documentation

#### <Solution>.Application (Application Logic)
- **Commands**: Write operations (Create, Update, Delete)
- **Queries**: Read operations (Get, List, Search)
- **Handlers**: MediatR handlers implementing `IRequestHandler<TRequest, TResponse>`
- **Services**: Application-level services
- **Interfaces**: Contracts for application services

**Key Patterns**:
- CQRS (Command Query Responsibility Segregation) with MediatR
- Queries as `record` types (immutable)
- Handlers orchestrate repository calls and mapping
- Single Responsibility: one handler per operation

**Example Handler**:
```csharp
public class GetItemsHandler : IRequestHandler<GetItemsQuery, IEnumerable<ItemDTO>>
{
    private readonly IItemRepository _itemRepository;
    private readonly IMapper _mapper;
    private readonly IContextUser _contextUser;
    private readonly IUnitOfWork _unitOfWork;
    private readonly AppSettings _settings;

    public async Task<IEnumerable<ItemDTO>> Handle(GetItemsQuery request, CancellationToken cancellationToken)
    {
        var sf = new SecurityFilters(_contextUser);
        var filter = sf.Structure(request.Filter);
        filter = SecurityFilters.Context(_unitOfWork, _settings, filter);

        var items = await _itemRepository.GetItems(
            cancellationToken, request.PageNumber, request.PageSize,
            request.SortColumn, request.SortDirection, filter);

        return _mapper.Map<IEnumerable<ItemDTO>>(items);
    }
}
```

**Example Query**:
```csharp
public record GetItemsQuery(
    string SortColumn,
    string SortDirection,
    int PageNumber = 0,
    int PageSize = -1
) : IRequest<IEnumerable<ItemDTO>>
{
    public ItemFilter Filter { get; set; }
}
```

#### <Solution>.Domain (Domain Logic)
- **Entities**: Domain entities (ItemEntity, OrderEntity, etc.)
- **Models**: Domain models with business logic
- **Repositories/Abstract**: Repository interfaces (`IItemRepository`, `IBaseRepository`, etc.)
- **Repositories/Filters**: Filter objects (`ItemFilter`, `IStructureFilter`, `IContextFilter`)
- **Constants**: Domain constants (`ValidationType`, `Roles`, etc.)
- **Helpers**: Domain helpers (AppSettings, Extensions)

**Key Principles**:
- No dependencies on infrastructure or frameworks
- Entities are anemic (data containers)
- Business logic in domain models and services
- Interfaces define contracts

#### <Solution>.Infrastructure (Data Access)
- **Repositories**: Repository implementations inheriting from `BaseRepository`
- **Mappings**: FluentNHibernate mappings (`ClassMap<TEntity>`)
- **Startups**: NHibernate configuration, Dependency Injection setup
- **Unit**: UnitOfWork pattern implementation
- **Helpers**: Infrastructure utilities (ContextUser, Security)

**Key Patterns**:
- Repository Pattern with BaseRepository
- NHibernate ORM with Fluent Mappings
- UnitOfWork for transaction management
- AuditInterceptor for automatic auditing

#### <Solution>.Service (Business Services)
- **Services**: Business logic services
- **ModelsDTO**: Data Transfer Objects
- **Helpers**: Business helpers (SecurityFilters, validators, etc.)
- **Validators**: Business rule validation

**Key Patterns**:
- DTOs for cross-layer communication
- Helper classes for complex business logic
- Validators for domain rules

## NHibernate ORM

### Configuration (NHibernateExtension.cs)

```csharp
services.AddNHibernate(connectionString)
    .Configure()
    .Database(MsSql2012 with MsSql2017CustomDialect)
    .DefaultSchema("dbo")
    .ExposeConfiguration(cfg => {
        cfg.SetProperty("CommandTimeout", "60000");
        cfg.SetProperty("UseSqlComments", "true");
        cfg.SetProperty("default_datetime_sql_type", "datetime");
    })
    .Cache(UseQueryCache, UseSecondLevelCache, HashtableCacheProvider)
    .Mappings(FluentMappings from Assembly)
    .BuildSessionFactory();

// ISession with AuditInterceptor
services.AddScoped<ISession>(sp => {
    sessionFactory.WithOptions()
        .Interceptor(auditInterceptor)
        .OpenSession();
});
```

**Key Features**:
- **SQL Server 2017 Dialect**: Custom dialect for modern SQL Server features
- **AuditInterceptor**: Automatic auditing of Create/Update metadata
- **Query Cache + Second-Level Cache**: Performance optimization
- **Command Timeout**: 60 seconds for long-running queries
- **SQL Comments**: Generated SQL includes comments for debugging

### Fluent Mappings Pattern

All entity mappings use FluentNHibernate's `ClassMap<T>`:

```csharp
public class ItemMap : ClassMap<ItemEntity>
{
    public ItemMap()
    {
        Schema("app");
        Table("Items");  // Can be view or table

        Id(x => x.Id, "ID")
            .GeneratedBy.Identity();

        Map(x => x.ExternalId, "EXTERNAL_ID")
            .Not.Nullable();

        References(x => x.Owner, "OWNER_ID")
            .Not.Nullable()
            .LazyLoad()
            .Cascade.None();

        HasOne(x => x.Details)
            .PropertyRef(x => x.Item)
            .LazyLoad()
            .Cascade.SaveUpdate();
    }
}
```

**Conventions**:
- `Schema("<schema>")` when tables live outside the default schema (e.g. legacy tables)
- Column names uppercase (e.g., `"PERSON_ID"`)
- LazyLoad for related entities (performance)
- Cascade rules explicit (None, SaveUpdate, All)
- References for many-to-one
- HasOne for one-to-one
- HasMany for one-to-many

**Location**: `<Solution>.Infrastructure/Mappings/**/*.cs`

## Repository Pattern

### BaseRepository

All repositories inherit from `BaseRepository`:

```csharp
public partial class BaseRepository : IBaseRepository
{
    protected readonly ISession Session;
    protected readonly IMapper Mapper;
    protected readonly string[] AuditableColumns = { "CreateFirm", "CreateUser", "UpdateUser", "CreateTime", "UpdateTime" };

    public BaseRepository(ISession session, IMapper mapper)
    {
        Session = session;
        Mapper = mapper;
    }

    protected UserEntity GetUser(string subId) { /* ... */ }
    protected StructureRights GetUserRolesStructureRights(string subId = null) { /* ... */ }
    protected bool IsSimple(Type type) { /* ... */ }
}
```

**Capabilities**:
- Direct `ISession` access for NHibernate queries
- `IMapper` for entity → DTO mapping
- User context methods (`GetUser`, `GetUserRolesStructureRights`)
- Audit support (AuditableColumns filtering)

### Repository Implementations

Repositories implement domain interfaces:

```csharp
public class ItemRepository : BaseRepository, IItemRepository
{
    public async Task<IEnumerable<ItemEntity>> GetItems(
        CancellationToken cancellationToken,
        int pageNumber,
        int pageSize,
        string sortColumn,
        string sortDirection,
        ItemFilter filter)
    {
        var query = BuildItemsQuery(filter);
        query = ApplySorting(query, sortColumn, sortDirection);
        query = ApplyPagination(query, pageNumber, pageSize);

        return await query.ListAsync(cancellationToken);
    }
}
```

**Best Practices**:
- Async methods with `CancellationToken`
- Server-side pagination (OFFSET/FETCH in SQL)
- Filtering via domain filter objects
- Sorting via string column names + direction
- Use NHibernate QueryOver or HQL for complex queries

**Location**: `<Solution>.Infrastructure/Repositories/**/*.cs`

## CQRS with MediatR

### Query/Command Pattern

**Queries** (read operations):
```csharp
public record GetItemsQuery(
    string SortColumn,
    string SortDirection,
    int PageNumber = 0,
    int PageSize = -1
) : IRequest<IEnumerable<ItemDTO>>
{
    public ItemFilter Filter { get; set; }
}
```

**Commands** (write operations):
```csharp
public record UpdateItemsCommand(
    IEnumerable<UpdateItemRequest> Items
) : IRequest<IEnumerable<UpdateItemResponse>>;
```

**Handlers**:
```csharp
public class GetItemsHandler : IRequestHandler<GetItemsQuery, IEnumerable<ItemDTO>>
{
    public async Task<IEnumerable<ItemDTO>> Handle(GetItemsQuery request, CancellationToken cancellationToken)
    {
        // 1. Apply security filters (SecurityFilters)
        // 2. Call repository
        // 3. Map to DTOs
        // 4. Return
    }
}
```

**MediatR Registration** (Startup.cs):
```csharp
services.AddMediatRExtension();
```

**Controller Usage**:
```csharp
[HttpGet]
public async Task<IActionResult> GetItems([FromQuery] GetItemsQuery query)
{
    var result = await _mediator.Send(query);
    return Ok(result);
}
```

**Benefits**:
- Decoupling of request/response from controllers
- Single Responsibility (one handler per operation)
- Testability (handlers can be unit tested in isolation)
- Pipeline behaviors (logging, validation, transactions)

## Security Filtering (SecurityFilters)

### Two-Stage Filtering

All queries pass through a filter-merging helper (here `SecurityFilters`) for security:

**1. Structure Filtering** (User Permissions):
```csharp
public TFilters Structure<TFilters>(TFilters filter) where TFilters : IStructureFilter
{
    // Merge user permissions with request filters
    filter.Organizations = filter.Organizations?.Intersect(_contextUser.Permissions.Organizations);
    filter.Departments = filter.Departments?.Intersect(_contextUser.Permissions.Departments);
    return filter;
}
```

**2. Context Filtering** (Role-based Logic):
```csharp
public static TFilters Context<TFilters>(IUnitOfWork unitOfWork, AppSettings settings, TFilters filter)
    where TFilters : IContextFilter
{
    var hasItemId = filter is ItemFilter f && f.Id.HasValue;

    if (filter.ContextRole == Roles.Manager)
    {
        // Force active period UNLESS filtering by explicit Id (historical lookup)
        if (!hasItemId && !filter.Period.HasValue)
            filter.Period = unitOfWork.ParamRepository.GetActivePeriod();
    }
    else if (filter.ContextRole == Roles.Viewer)
    {
        filter.Statuses = settings.Viewer.AllowedStatuses;
    }
    else if (filter.ContextRole == Roles.Owner)
    {
        filter.OwnerSubId = filter.ContextSubjectId;
    }
    // ... other roles

    return filter;
}
```

**Key Principle**: Filtering by data context (user permissions, role, active period) is applied centrally, not per query. Any bypass (e.g. explicit lookup by Id that skips the active-period filter) must be explicit, narrow and documented.

**Implementation**: `<Solution>.Service/Helpers/SecurityFilters.cs`

## AutoMapper Integration

### Profile Registration (Startup.cs)

```csharp
services.AddAutoMapper(cfg => {
    cfg.AddProfiles(AutoMapperProfiles);
});

private static IEnumerable<Profile> AutoMapperProfiles => [
    ...AutomapperApiConfiguration.GetProfiles(),      // API layer
    ...AutomapperServiceConfiguration.GetProfiles(),  // Service layer
    ...AutomapperInfrastructureConfiguration.GetProfiles() // Infrastructure layer
];
```

### Mapping Layers

**Infrastructure → Service** (Entity → DTO):
```csharp
public class InfrastructureProfile : Profile
{
    public InfrastructureProfile()
    {
        CreateMap<ItemEntity, ItemDTO>();
    }
}
```

**Service → API** (DTO → API Model):
```csharp
public class ApiProfile : Profile
{
    public ApiProfile()
    {
        CreateMap<ItemDTO, ItemModel>();
    }
}
```

**Location**: `<Solution>.*/MapperProfiles/**/*.cs`

## UnitOfWork Pattern

```csharp
public interface IUnitOfWork
{
    IParamRepository ParamRepository { get; }
    IAcademicYearRepository AcademicYearRepository { get; }
    // ... other repositories
}
```

**Usage**:
```csharp
var activeYear = _unitOfWork.ParamRepository.GetActiveYear();
```

**Benefits**:
- Centralized repository access
- Transaction management
- Consistency across operations

**Implementation**: `<Solution>.Infrastructure/Unit/`

## Dependency Injection

### Service Registration (Startup.cs)

```csharp
services.AddAppsettings(Configuration);        // AppSettings binding
services.AddMailSender();                      // Email service
services.AddHttpContextAccessor();             // HTTP context
services.AddOpenId(authority, audience);       // Authentication
services.AddSwagger(openApiConfig);            // API documentation
services.AddInfrastructureServices(connStr);     // NHibernate + Repositories
services.AddMigrator(connStr);                 // FluentMigrator
services.AddMediatRExtension();                // CQRS handlers
services.AddAutoMapper(cfg => ...);            // AutoMapper
services.AddCors(options => ...);              // CORS policy
services.AddApplicationInsightsTelemetry(...); // Monitoring
services.AddSignalR();                         // Real-time communication
```

### Extension Methods

Organized in `*/Startups/*Extension.cs` files:
- `NHibernateExtension.cs` - NHibernate + ISession
- `RepositoriesExtension.cs` - Repository registrations
- `AutomapperServiceConfiguration.cs` - AutoMapper profiles

## Audit Interceptor

AuditInterceptor automatically populates audit fields on save:

```csharp
public class AuditInterceptor : IAuditInterceptor
{
    public bool OnSave(object entity, object id, object[] state, string[] propertyNames, IType[] types)
    {
        // Set CreateUser, CreateTime, CreateFirm
        if (entity is IAuditable)
        {
            state[Array.IndexOf(propertyNames, "CreateUser")] = currentUser;
            state[Array.IndexOf(propertyNames, "CreateTime")] = DateTime.UtcNow;
        }
    }
}
```

**Auditable Columns**:
- `CreateFirm`, `CreateUser`, `CreateTime`
- `UpdateUser`, `UpdateTime`

**Implementation**: `<Solution>.Infrastructure/Startups/AuditInterceptor.cs`

## Key Architectural Principles

1. **Separation of Concerns**: Each layer has clear responsibilities
2. **Dependency Inversion**: Layers depend on abstractions (interfaces), not concretions
3. **Single Responsibility**: Each handler/repository/service has one reason to change
4. **DRY (Don't Repeat Yourself)**: BaseRepository, SecurityFilters, AutoMapper reduce duplication
5. **Security by Default**: All queries pass through SecurityFilters (user permissions + role context)
6. **Async/Await**: All I/O operations are asynchronous with CancellationToken support
7. **Explicit Mappings**: AutoMapper profiles make layer transitions clear
8. **Testability**: Constructor injection enables unit testing (mocking ISession, IMapper, etc.)
9. **Audit Trail**: AuditInterceptor ensures all changes are tracked
10. **Legacy Integration**: NHibernate allows flexible mapping to legacy schemas

## Common Patterns

### Handler Pattern
```csharp
public async Task<TResponse> Handle(TRequest request, CancellationToken cancellationToken)
{
    // 1. Apply security filters
    var filter = new SecurityFilters(_contextUser).Structure(request.Filter);
    filter = SecurityFilters.Context(_unitOfWork, _settings, filter);

    // 2. Call repository
    var entities = await _repository.GetEntities(filter, cancellationToken);

    // 3. Map to DTOs
    var dtos = _mapper.Map<IEnumerable<TDto>>(entities);

    // 4. Return
    return dtos;
}
```

### Repository Query Pattern
```csharp
public async Task<IEnumerable<TEntity>> GetEntities(TFilter filter, CancellationToken ct)
{
    var query = Session.QueryOver<TEntity>();

    // Apply filters
    if (filter.SomeProperty.HasValue)
        query = query.Where(x => x.SomeProperty == filter.SomeProperty);

    // Apply sorting
    query = ApplySort(query, filter.SortColumn, filter.SortDirection);

    // Apply pagination (server-side)
    if (filter.PageSize > 0)
        query = query.Skip(filter.PageNumber * filter.PageSize).Take(filter.PageSize);

    return await query.ListAsync(ct);
}
```

### Pagination with Count
```csharp
// Count query (for X-Results-Count header)
var countQuery = Session.QueryOver<TEntity>()
    .Where(/* same filters */)
    .SelectList(list => list.SelectCount(x => x.Id))
    .FutureValue<int>();

// Data query
var dataQuery = Session.QueryOver<TEntity>()
    .Where(/* same filters */)
    .Skip(pageNumber * pageSize)
    .Take(pageSize)
    .Future<TEntity>();

// Both queries execute in single round-trip
var count = await countQuery.GetValueAsync(ct);
var data = await dataQuery.GetEnumerableAsync(ct);
```

---

**Summary**: .NET Core backend using Clean Architecture with 5 layers (API/Application/Domain/Infrastructure/Service), NHibernate ORM with Fluent Mappings, CQRS via MediatR, Repository Pattern, AutoMapper for layer transitions, and comprehensive security filtering via SecurityFilters. All operations audited, async, testable, and secured by user permissions.
