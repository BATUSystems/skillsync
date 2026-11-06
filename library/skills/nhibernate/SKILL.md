---
name: nhibernate
description: NHibernate ORM best practices including SessionFactory configuration, mapping strategies, query optimization, relationship management, and performance patterns
user-invokable: true
---

# NHibernate Best Practices

This skill provides guidance on using NHibernate ORM effectively in .NET APIs. Covers SessionFactory setup, entity mapping, LINQ/HQL queries, performance optimization, and integration with Clean Architecture patterns.

## SessionFactory & Configuration Design

### Setup & Lifetime Management
- **SessionFactory** is expensive to create - instantiate once at application startup and reuse
- Cache SessionFactory in dependency injection container (singleton)
- Configure FluentNHibernate or XML mappings during bootstrap only
- Use `SessionFactory.WrappedInstance` for low-level NHibernate access

### Configuration Best Practices
- Store connection strings in configuration (appsettings.json)
- Use flyway, migrate.exe, or custom migration tools for schema management
- Set `show_sql=true` in development, `false` in production
- Configure `current_session_context_class` for web apps (e.g., "wcf" or "async_local")
- Enable statistics and logging via ILoggerFactory in development

### Example Configuration (FluentNHibernate)
```csharp
// Bootstrap
var sesFactory = Fluently.Configure()
    .Database(MsSqlConfiguration.MsSql2016
        .ConnectionString(connString)
        .AdoNetBatchSize(20))
    .Mappings(m => m.FluentMappings.AddFromAssemblyOf<UserMap>())
    .ExposeConfiguration(cfg => {
        var schemaUpdate = new SchemaUpdate(cfg);
        schemaUpdate.Execute(false, true); // Create tables if missing
    })
    .BuildSessionFactory();
```

## Entity Mapping

### Fluent Mapping Strategy
- Create `*Map.cs` classes for each entity in `Infrastructure/NHibernate/Mappings/`
- Each map inherits from `ClassMap<T>` (FluentNHibernate)
- Implement `Id()`, relationships, and property configuration
- Keep mappings focused: one entity = one map file

### ID Strategy
- **Natural Keys**: Use identity values when they exist (e.g., student ID)
- **Surrogate Keys**: Use `GUID().GeneratedBy.GuidComb()` or `Identity()` for surrogate keys
- Configure `generator` explicitly: `Identity()`, `Assigned()`, `GuidComb()`, or custom
- For composites: use `CompositeId()` (rare)

### Identity Columns in Legacy Tables

Legacy tables often use IDENTITY columns for auto-generated primary keys.

**Correct Mapping** (FluentNHibernate):
```csharp
Id(x => x.Id, "ID_LEGACY").GeneratedBy.Identity();  // Let DB assign ID
```

This tells NHibernate:
1. Do NOT require ID to be set before Save()
2. Allow NULL ID during object construction
3. After INSERT, retrieve generated ID using `SCOPE_IDENTITY()`

**WRONG** (causes NHibernate ID assignment exception):
```csharp
Id(x => x.Id, "ID_LEGACY");  // <- Default is Assigned generator
// Result: NHibernate expects ID manually assigned:
//   var entity = new MyEntity { Id = 123, ... };  // Required!
```

**Common Error**:
```
NHibernate.Id.IdentifierGenerationException: ids for this class must be manually assigned
```
Solution: Add `.GeneratedBy.Identity()` to mapping if table has IDENTITY PK.



### Entity Design Principles
- **Lazy Loading**: NHibernate lazy loads by default (collections are proxied)
- Map all relationships explicitly: one-to-one, one-to-many, many-to-many
- Use collections (ISet<T>, IList<T>) for navigation properties - NOT ICollection<T>
- For read-only scenarios, use projections instead of loading full entities
- Consider backing fields for complex logic (e.g., `_students` with `Students` property)

### Relationship Mapping
```csharp
// One-to-Many
public class InstitutionMap : ClassMap<Institution>
{
    public InstitutionMap()
    {
        Id(x => x.Id);
        Map(x => x.Name);
        
        // One-to-Many: Institution has many Awards
        HasMany(x => x.Awards)
            .Cascade.AllDeleteOrphan()
            .Inverse()
            .KeyColumn("InstitutionId");
    }
}

// Many-to-One (reverse side)
public class AwardMap : ClassMap<Award>
{
    public AwardMap()
    {
        Id(x => x.Id);
        References(x => x.Institution)
            .NotNull()
            .Column("InstitutionId");
    }
}
```

### Special Cases
- **Owned Entity Types**: Use `Component()` for value objects (addresses, contact info)
- **Inheritance**: Use table-per-hierarchy (TPH) or table-per-subclass (TPS)
- **Enums**: Use `Map(x => x.Status).CustomType<EnumType<StudentStatus>>()`
- **Collections on value objects**: Keep value objects simple; avoid collections

## Querying

### LINQ Queries (IQueryable)
- Use LINQ-to-NHibernate for type-safe queries
- Understand lazy evaluation: queries execute on `.ToList()`, `.FirstOrDefault()`, etc.
- Compose queries before calling terminal operators
- For complex filtering, build queryable incrementally

### HQL (Hibernate Query Language)
- Use when LINQ doesn't support specific SQL operations
- `session.Query<Entity>().Where(...).ToList()` (LINQ - preferred)
- `session.CreateQuery("from Entity e where ...").List<Entity>()` (HQL)
- Parameterize queries to prevent SQL injection

### Query Patterns
```csharp
// LINQ - Preferred for type-safety
var students = session.Query<Student>()
    .Where(s => s.Year >= 2020 && s.Status == StudentStatus.Active)
    .OrderBy(s => s.Surname)
    .Take(100)
    .ToList();

// Projection - Only fetch needed fields
var studentNames = session.Query<Student>()
    .Where(s => s.Year >= 2020)
    .Select(s => new { s.Id, s.FirstName, s.Surname })
    .ToList();

// HQL - For complex operations
var result = session.CreateQuery(
    "select s.Id, s.Surname from Student s where s.Year = :year")
    .SetParameter("year", 2024)
    .List<object[]>();
```

## Eager Loading & Lazy Loading

### Avoid N+1 Queries
- Problem: Loading parent + triggering child collections one-by-one
- Solution: Use `.Fetch()` for explicit eager loading

```csharp
// ❌ N+1 problem
var institutions = session.Query<Institution>().ToList();
foreach (var inst in institutions)
    var count = inst.Awards.Count; // Separate query per institution

// ✅ Eager load
var institutions = session.Query<Institution>()
    .Fetch(x => x.Awards)
    .ToList();
```

### Projection Over Entity Fetching
- For read-only scenarios, use `.Select()` to fetch only required fields
- Reduces memory footprint and database load
- More efficient than lazy-loading collections

## Performance Optimization

### Query Optimization
- Use `.AsNoTracking()` equivalent: `session.Query<Entity>().Where(...).ToList()` (implicit no-tracking)
- For updates, use tracking: `session.Query<Entity>().Where(...).ToList()` then `.SaveOrUpdate()`
- Pagination: Use `.Skip()` and `.Take()` for large datasets
- Batch queries: Load multiple independent datasets in one roundtrip (use futures - advanced)

### Concurrency & Locking
- Use optimistic locking: add `Version` property, map with `.Version()`
- Check `StaleObjectStateException` on save
- NHibernate handles dirty checking automatically (no explicit flush needed in web app)

### Caching
- First-level cache (Session scope): Automatic, transparent
- Second-level cache: Configure via QueryCache or distributed cache (Redis with NHibernate provider)
- Query result cache: Limited use; cache projections instead of entities

```csharp
// Version for optimistic locking
public class Entity
{
    public virtual int Version { get; set; }
}

// Map it
Map(x => x.Version).Not.Insert().Not.Update();
```

## Transactions & Session Management

### Session Scope in Web Apps
- Create one Session per request
- Configure `current_session_context_class` to bind session to HTTP context
- Use `sessionFactory.GetCurrentSession()` to retrieve session for request
- Commit transaction at end of request (in middleware or action filter)

### Transaction Handling
```csharp
using (var session = sessionFactory.OpenSession())
using (var tx = session.BeginTransaction())
{
    try
    {
        session.SaveOrUpdate(entity);
        session.Flush(); // Force immediate execution
        tx.Commit();
    }
    catch
    {
        tx.Rollback();
        throw;
    }
}
```

### Batch Operations
- Update multiple entities efficiently
- Use HQL bulk update: `session.CreateQuery("update Entity e set e.Status = :status where ...").ExecuteUpdate()`
- Avoid loading all entities into memory for updates

## Change Tracking & Saving

### Dirty Checking
- NHibernate tracks changes automatically (no explicit mark needed)
- Call `session.Flush()` to execute pending statements
- Call `session.Clear()` to clear first-level cache and stop tracking
- Use `IStatelessSession` for bulk operations (no tracking)

### SaveOrUpdate vs Save
- `SaveOrUpdate()`: Insert if new, update if existing
- `Save()`: Always insert (throws if already has ID)
- `Update()`: Always update
- Use `SaveOrUpdate()` for general persistence

## Clean Architecture Integration

### Repository Pattern with NHibernate
- Wrap `ISession` queries in Repository<T> methods
- Return `IQueryable<T>` for complex queries (specification pattern)
- Use Fluent APIs in repositories, keep domains clean
- Use automapper for DTO conversions

### Example Repository
```csharp
public class StudentRepository : IRepository<Student>
{
    private readonly ISession _session;
    
    public StudentRepository(ISession session) => _session = session;
    
    public Student GetById(int id) => 
        _session.Query<Student>().FirstOrDefault(s => s.Id == id);
    
    public IQueryable<Student> GetAll() => 
        _session.Query<Student>();
    
    public void Add(Student student) => _session.SaveOrUpdate(student);
    
    public void Remove(Student student) => _session.Delete(student);
}
```

### CQRS Pattern
- Queries: Read-only using `.ToList()` on queryables (no tracking)
- Commands: Use transactional sessions for saves/updates
- Consider query objects for complex reads (separate session)

## Security

### SQL Injection Prevention
- Always parameterize queries
- NHibernate handles parameterization automatically in LINQ/HQL
- Avoid string concatenation in HQL

```csharp
// ✅ Safe - parameterized
var result = session.CreateQuery("from Student where Surname = :surname")
    .SetParameter("surname", userInput)
    .List<Student>();

// ❌ Dangerous - string concat
var result = session.CreateQuery($"from Student where Surname = '{userInput}'")
    .List<Student>();
```

### Access Control
- Implement filtering in repository layer
- Restrict queries to the user's data (tenant/organization/role context)
- Use application-level filtering before returning entities

## Testing

### Unit Testing with NHibernate
- Mock `ISession` and `IQueryable<T>` results
- Use `InMemoryTestDatabase` or test fixtures for integration tests
- Test repositories separately from domain logic

### Integration Testing
- Use SQLite in-memory database for fast integration tests
- Or use transactional rollback with real SQL Server (slower)
- Seed test data via migrations or fixtures

```csharp
// SQLite in-memory for tests
var cfg = Fluently.Configure()
    .Database(SQLiteConfiguration.Standard.InMemory())
    .Mappings(m => m.FluentMappings.AddFromAssemblyOf<StudentMap>())
    .BuildSessionFactory();
```

## Related Skills

- See **backend-architecture** for Clean Architecture patterns used with NHibernate
- See **testing-guidelines** for testing NHibernate repositories
- See **sql-optimization** for performance tuning of generated queries

## External Resources

- [NHibernate Documentation](https://nhibernate.info/)
- [FluentNHibernate Wiki](https://wiki.fluentnhibernate.org/)
- [NHibernate Best Practices](https://www.hibernate.org/orm/documentation)
- [NHibernate LINQ Provider](https://nhibernate.info/doc/nhibernate-reference/querylinq.html)

---

**Summary**: Keep SessionFactory as singleton, use FluentMapping for configurations, compose LINQ queries for type-safety, eager load relationships to avoid N+1 queries, leverage dirty checking for automatic tracking, and integrate with Clean Architecture repositories for maintainability.
