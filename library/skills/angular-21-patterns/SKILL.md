---
name: angular-21-patterns
description: Angular 21.x modern best practices, patterns, and production-ready implementations. Covers Signals, Standalone Components, Type-Safe Forms, Performance Optimization, and RxJS patterns.
user-invokable: true
---

# Angular 21.x Best Practices és Design Patterns - Comprehensive Guide

> **Version**: 1.0  
> **Angular Version**: 21.x (Current as of 26 Feb 2026)  
> **Last Updated**: Feb 2026  
> **Status**: Production-Ready

## Spis treści

Dokumentacja obejmuje następujące zagadnienia:

1. Wprowadzenie
2. Architektura komponentów
3. Signals zamiast RxJS
4. Reactive Forms i TypeScript
5. Performance Optimization
6. RxJS Patterns
7. Linting i Code Quality
8. Template Best Practices
9. Dependency Injection
10. Praktyczne przykłady

---

## Wprowadzenie

Angular 21 wprowadził rewolucyjne zmiany w architekturze frameworku. Największe zmiany to:

- **Standalone components** jako standard (nie NgModules)
- **Signals API** - całkowicie nowy system reaktywności
- **Dependency Injection z `inject()` funkcją**
- **Signal Forms** - nowoczesna alternatywa dla Reactive Forms
- **Flat Config ESLint** - nowy standard lintingu

Ta dokumentacja fokusuje się na **nowoczesnych, production-ready praktykach** dla Angular 21+.

> Uwaga: w Angular CLI / VS Code `tsconfig.app.json` z `outDir` często wymaga jawnie ustawionego `rootDir`, żeby edytor nie zgłaszał fałszywych błędów projektu TypeScript.
>
> Uwaga: w aplikacjach PWA warto zarejestrować service workera i przechwytywać `beforeinstallprompt` wewnątrz singletonowego serwisu rootowego. W `provideHttpClient(withInterceptors([...]))` `pwaNetworkInterceptor` powinien być zarejestrowany przed `jwtInterceptor`, aby offline-friendly fallback działał zanim auth interceptor zwróci błąd.
>
> Pliki `references/org-*.md`, jeśli są, to rozszerzenia warstwy organizacji: przeczytaj te, które pasują do projektu.

---

## Architektura komponentów

### Standalone Components

Wszystkie komponenty w Angular 21 powinny być **standalone** z domyślnym `true`:

```typescript
import { Component, input, output } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-user-profile',
  standalone: true,
  imports: [CommonModule], // Importuj tylko co potrzebujesz
  template: `
    <div class="profile">
      <h1>{{ userName() }}</h1>
      <button (click)="onSave()">Save</button>
    </div>
  `,
  styles: [`
    .profile {
      padding: 1rem;
      border: 1px solid #ccc;
    }
  `]
})
export class UserProfileComponent {
  // Nowoczesne API dla inputs/outputs
  userName = input<string>('Guest');
  userSaved = output<void>();
  
  onSave() {
    this.userSaved.emit();
  }
}
```

### Ścieżka i Organizacja Plików

Struktura projektu powinna być oparta na **feature modules**:

```
src/
├── main.ts
├── app.component.ts
│
├── features/
│   ├── user/
│   │   ├── user-profile/
│   │   │   ├── user-profile.component.ts
│   │   │   ├── user-profile.component.html
│   │   │   ├── user-profile.component.css
│   │   │   └── user-profile.component.spec.ts
│   │   │
│   │   └── user.service.ts
│   │
│   └── products/
│       ├── product-list/
│       │   ├── product-list.component.ts
│       │   └── ...
│       └── product.service.ts
│
├── shared/
│   ├── directives/
│   ├── pipes/
│   └── utilities/
│
└── styles/
    ├── variables.css
    └── global.css
```

**Reguły organizacji:**
- Jeden główny koncept na plik
- Komponent + template + styles razem w folderze
- Testami obok oryginalnego pliku (`.spec.ts`)
- Unikaj folderów "components", "directives", "services" na wymuszonym poziomie

---

## Signals zamiast RxJS

### Czym są Signals?

Signals to **granular reactivity system** - nowoczesna alternatywa dla RxJS w wielu przypadkach:

```typescript
import { Component, signal, computed, effect } from '@angular/core';

@Component({
  selector: 'app-counter',
  standalone: true,
  template: `
    <div>
      <p>Count: {{ count() }}</p>
      <p>Doubled: {{ doubledCount() }}</p>
      <button (click)="increment()">+1</button>
      <button (click)="setCount(0)">Reset</button>
    </div>
  `
})
export class CounterComponent {
  // Writable Signal -Source of truth
  count = signal(0);
  
  // Computed Signal - Derived reactive state
  doubledCount = computed(() => this.count() * 2);
  
  // Side Effects - React to changes
  constructor() {
    effect(() => {
      console.log(`Count changed to ${this.count()}`);
    });
  }
  
  increment() {
    // Aktualizuj wartość
    this.count.update(v => v + 1);
  }
  
  setCount(value: number) {
    this.count.set(value);
  }
}
```

### Opuszczanie Read-Only Signals

```typescript
export class CounterState {
  private readonly _count = signal(0);
  
  // Expose tylko read-only wersję
  readonly count = this._count.asReadonly();
  
  // Metody do modyfikacji
  increment() {
    this._count.update(v => v + 1);
  }
}
```

### Signal Effects

```typescript
import { effect, signal, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';

@Component({
  selector: 'app-user-loader'
})
export class UserLoaderComponent {
  private http = inject(HttpClient);
  
  userId = signal<number | null>(null);
  userData = signal<any>(null);
  
  constructor() {
    // Effect - Automatycznie uruchamia się kiedy userId się zmieni
    effect(() => {
      const id = this.userId();
      if (id) {
        this.http.get(`/api/users/${id}`).subscribe(data => {
          this.userData.set(data);
        });
      }
    });
  }
  
  loadUser(id: number) {
    this.userId.set(id);
  }
}
```

### Reactive Contexts

Signals są śledzone tylko w **reactive contexts**:

- Inside `component/directive templates`
- Inside `computed()` derivations
- Inside `effect()` callbacks
- Inside `linkedSignal()` derivatives

```typescript
// ❌ Błąd - zmiany nie będą śledzone
setTimeout(() => {
  const value = mySignal(); // Nie w reactive context!
}, 1000);

// ✅ Dobry wzór - call signal PRZED await
effect(async () => {
  const id = userId(); // Przeczytaj signal PRZED async!
  const data = await fetchUser(id);
  console.log(data);
});
```

---

## Reactive Forms i TypeScript

### Strictly Typed Reactive Forms

Angular 21 wspiera **fully typed reactive forms** z `FormControl<T>`:

```typescript
import { Component, inject } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';

interface UserForm {
  firstName: string;
  email: string;
  age: number;
}

@Component({
  selector: 'app-user-form',
  standalone: true,
  imports: [ReactiveFormsModule],
  template: `
    <form [formGroup]="form" (ngSubmit)="onSubmit()">
      <input formControlName="firstName" placeholder="First Name" />
      
      @if (form.get('firstName')?.hasError('required')) {
        <span>First name is required</span>
      }
      
      <input formControlName="email" type="email" />
      <input formControlName="age" type="number" />
      
      <button type="submit" [disabled]="form.invalid">Submit</button>
    </form>
  `
})
export class UserFormComponent {
  private fb = inject(FormBuilder);
  
  form = this.fb.group<UserForm>({
    firstName: ['', Validators.required],
    email: ['', [Validators.required, Validators.email]],
    age: [0, [Validators.min(18), Validators.max(120)]]
  });
  
  onSubmit() {
    if (this.form.valid) {
      const data: UserForm = this.form.getRawValue();
      console.log(data); // Type-safe!
    }
  }
}
```

### Signal Forms (Experimental)

Nowy eksperymentalny system Forms oparty na Signals:

```typescript
import { Component, inject, signal } from '@angular/core';
import { FormField } from '@angular/forms';

@Component({
  selector: 'app-signal-form'
})
export class SignalFormComponent {
  firstName = signal('');
  email = signal('');
  
  firstNameField = new FormField(
    this.firstName, 
    { validators: [required] }
  );
  
  get isValid() {
    return this.firstNameField.valid() && 
           this.email().length > 0;
  }
}
```

---

## Performance Optimization

### OnPush Change Detection

**Zawsze** używaj `OnPush` dla performanty:

```typescript
import { Component, ChangeDetectionStrategy, input } from '@angular/core';

@Component({
  selector: 'app-product-card',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="card">
      <h3>{{ product().name }}</h3>
      <p>{{ product().price | currency }}</p>
    </div>
  `
})
export class ProductCardComponent {
  product = input.required<Product>();
}
```

**OnPush zmienia się kiedy:**
- Nowy input jest przypisany (template binding)
- Event handler w komponencie lub dzieciach
- Signal używany w template się zmieni

### Skipping Component Subtrees

```typescript
import { Component, ChangeDetectionStrategy, OnPush } from '@angular/core';

// Rodzic bez OnPush
@Component({
  selector: 'app-dashboard',
  template: `
    <!-- Change detection tutaj zawsze uruchamia się -->
    <app-sidebar (menuClick)="onMenuClick()"></app-sidebar>
    <!-- Ale ta lista z OnPush będzie pominięta jeśli nie ma nowych inputs -->
    <app-product-list [products]="products"></app-product-list>
  `
})
export class DashboardComponent {
  products = [...];
  onMenuClick() { ... }
}

// Dziecko z OnPush
@Component({
  selector: 'app-product-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ProductCardComponent]
})
export class ProductListComponent {
  products = input<Product[]>([]);
}
```

### Memoization z Computed Signals

```typescript
import { computed, input, signal } from '@angular/core';

@Component({
  selector: 'app-search-results'
})
export class SearchResultsComponent {
  items = input<Item[]>([]);
  searchTerm = signal('');
  
  // Computed automatycznie cachuje rezultat
  filteredItems = computed(() => {
    const term = this.searchTerm().toLowerCase();
    return this.items().filter(item => 
      item.name.toLowerCase().includes(term)
    );
  });
}
```

### Lazy Loading i @defer

```typescript
import { Component, defer } from '@angular/core';
import { HeavyComponent } from './heavy.component';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  template: `
    <h1>Dashboard</h1>
    
    <!-- Component ładuje się tylko kiedy scrollujesz do niego -->
    @defer (on viewport) {
      <app-heavy />
    } @placeholder {
      <p>Loading analytics...</p>
    }
  `,
  imports: [HeavyComponent]
})
export class DashboardComponent {}
```

---

## RxJS Patterns

### takeUntilDestroyed - Memory Leak Prevention

```typescript
import { Component, inject, DestroyRef } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NotificationService } from './notification.service';

@Component({
  selector: 'app-notifications'
})
export class NotificationsComponent {
  private notifications = inject(NotificationService);
  private destroyRef = inject(DestroyRef);
  
  constructor() {
    // Automatycznie się unsubscribe kiedy component zostaje zniszczony
    this.notifications.messages$
      .pipe(
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe(message => {
        console.log(message);
      });
  }
}
```

### toSignal - Bridge RxJS do Signals

```typescript
import { Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { DataService } from './data.service';

@Component({
  selector: 'app-user-data'
})
export class UserDataComponent {
  private dataService = inject(DataService);
  
  // Konwertuj Observable na Signal
  userData = toSignal(
    this.dataService.getUserData(), 
    { initialValue: null }
  );
  
  template: `
    @if (userData(); as user) {
      <p>{{ user.name }}</p>
    }
  `
}
```

### SwitchMap Pattern

```typescript
import { Component, inject, signal } from '@angular/core';
import { toObservable } from '@angular/core/rxjs-interop';
import { switchMap } from 'rxjs/operators';
import { ApiService } from './api.service';

@Component({
  selector: 'app-search'
})
export class SearchComponent {
  private api = inject(ApiService);
  
  searchTerm = signal('');
  
  // Automatycznie unsubscribe od starych requestów
  searchResults = toObservable(this.searchTerm).pipe(
    switchMap(term => this.api.search(term))
  );
}
```

### Unsubscribe Pattern (Legacy)

Jeśli musisz ręcznie zarządzać subskrypcjami:

```typescript
import { Component, OnInit, OnDestroy } from '@angular/core';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';

@Component({
  selector: 'app-legacy'
})
export class LegacyComponent implements OnInit, OnDestroy {
  private destroy$ = new Subject<void>();
  
  ngOnInit() {
    this.someObservable$
      .pipe(takeUntil(this.destroy$))
      .subscribe(value => console.log(value));
  }
  
  ngOnDestroy() {
    this.destroy$.next();
    this.destroy$.complete();
  }
}
```

---

## Linting i Code Quality

### ESLint Configuration (Angular 21 + ESLint v9)

```javascript
// eslint.config.js - Flat Config Format

import angular from 'angular-eslint/eslint-plugin';
import templatePlugin from 'angular-eslint/eslint-plugin-template';
import typescriptEslint from 'typescript-eslint';

export default [
  {
    files: ['**/*.ts'],
    extends: [
      ...typescriptEslint.configs.recommended,
      ...typescriptEslint.configs.stylistic,
    ],
    rules: {
      '@angular-eslint/directive-selector': [
        'error',
        {
          type: 'attribute',
          prefix: 'app',
          style: 'camelCase'
        }
      ],
      '@angular-eslint/component-selector': [
        'error',
        {
          type: 'element',
          prefix: 'app',
          style: 'kebab-case'
        }
      ],
      '@angular-eslint/no-empty-lifecycle-method': 'error',
      '@angular-eslint/prefer-standalone': 'error', // Wymuszaj standalone!
      '@angular-eslint/use-lifecycle-interface': 'error',
      '@typescript-eslint/no-implicit-any': 'error'
    }
  },
  {
    files: ['**/*.html'],
    extends: [templatePlugin.configs.recommended],
    rules: {
      '@angular-eslint/template/no-negated-async': 'error',
      '@angular-eslint/template/use-track-by-function': 'error'
    }
  }
];
```

### Critical ESLint Rules dla Angular

| Reguła | Cel | Status |
|--------|-----|--------|
| `prefer-standalone` | Wymuszaj standalone komponenty | **ERROR** |
| `use-lifecycle-interface` | Implementuj lifecycle interfaces | **ERROR** |
| `no-empty-lifecycle-method` | Nie pusty ngOnInit | **ERROR** |
| `use-track-by-function` | Track w `*ngFor` | **WARN** |
| `template/no-negated-async` | Nie neguj `async` pipe | **WARN** |

---

## Template Best Practices

### Control Flow (@if, @for, @switch)

```typescript
// Nowoczesna składnia Angular 21
@Component({
  template: `
    <!-- @if zamiast *ngIf -->
    @if (isLoading) {
      <p>Loading...</p>
    } @else {
      <!-- @for zamiast *ngFor -->
      @for (item of items; track item.id) {
        <div>{{ item.name }}</div>
      } @empty {
        <p>No items</p>
      }
    }
    
    <!-- @switch zamiast ngSwitch -->
    @switch (userRole) {
      @case ('admin') {
        <admin-panel />
      }
      @case ('user') {
        <user-dashboard />
      }
      @default {
        <guest-view />
      }
    }
  `
})
```

### Class/Style Bindings (Nie NgClass/NgStyle)

```typescript
// ❌ Stary sposób
<div [ngClass]="{ active: isActive, disabled: isDisabled }"></div>

// ✅ Nowy sposób - Szybszy i czytelniejszy
<div [class.active]="isActive()" [class.disabled]="isDisabled()"></div>

// Lub dla wielu klas
<div [class]="isActive() ? 'active large' : 'inactive small'"></div>

// Style bindings
<div [style.background-color]="backgroundColor()" 
     [style.width.%]="widthPercent()"></div>
```

### Event Handling

```typescript
// Dobrze - nazwy opisujące akcję
<button (click)="saveUserData()">Save</button>
<input (keydown.enter)="submitForm()" />
<button (click)="deleteUser($event)" />

// Złe - generyczne nazwy
<button (click)="handleClick()">Action</button>
```

### Using Signals w Templates

```typescript
@Component({
  template: `
    <p>Count: {{ count() }}</p>
    <p>Doubled: {{ doubledCount() }}</p>
    
    @if (count() > 5) {
      <p>Count is high!</p>
    }
    
    @for (item of items(); track item.id) {
      <div>{{ item.name }}</div>
    }
  `
})
export class CounterComponent {
  count = signal(0);
  doubledCount = computed(() => this.count() * 2);
  items = signal<Item[]>([]);
}
```

---

## Dependency Injection

### Inject Function (Nowoczesny Sposób)

```typescript
import { Component, inject, Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';

@Injectable({ providedIn: 'root' })
export class UserService {
  private http = inject(HttpClient);
  
  constructor() {
    // Dependency injection bez konstruktora!
  }
  
  getUser(id: string) {
    return this.http.get(`/api/users/${id}`);
  }
}

@Component({
  selector: 'app-user'
})
export class UserComponent {
  private userService = inject(UserService);
  private router = inject(Router);
  
  user = signal<User | null>(null);
  
  loadUser(id: string) {
    this.userService.getUser(id).subscribe(
      user => this.user.set(user)
    );
  }
}
```

### Provider Pattern

```typescript
// app.config.ts
import { ApplicationConfig, importProvidersFrom } from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideHttpClient } from '@angular/common/http';

export const appConfig: ApplicationConfig = {
  providers: [
    provideRouter(routes),
    provideHttpClient(),
    {
      provide: 'API_BASE_URL',
      useValue: 'https://api.example.com'
    }
  ]
};
```

### Injection Tokens

```typescript
import { InjectionToken, inject } from '@angular/core';

export const API_BASE_URL = new InjectionToken<string>(
  'API_BASE_URL', 
  { factory: () => 'https://api.example.com' }
);

@Injectable({ providedIn: 'root' })
export class ApiService {
  private baseUrl = inject(API_BASE_URL);
  
  getEndpoint() {
    return `${this.baseUrl}/v1`;
  }
}
```

---

## Praktyczne przykłady

### Complete User Management Feature

```typescript
// user.service.ts
import { Injectable, inject, signal, computed } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { toSignal } from '@angular/core/rxjs-interop';

export interface User {
  id: string;
  name: string;
  email: string;
  role: 'admin' | 'user';
}

@Injectable({ providedIn: 'root' })
export class UserService {
  private http = inject(HttpClient);
  private apiUrl = 'https://api.example.com/users';
  
  // State
  private _users = signal<User[]>([]);
  private _loading = signal(false);
  private _error = signal<string | null>(null);
  
  // Public read-only views
  readonly users = this._users.asReadonly();
  readonly loading = this._loading.asReadonly();
  readonly error = this._error.asReadonly();
  
  // Derived state
  readonly adminCount = computed(() => 
    this._users().filter(u => u.role === 'admin').length
  );
  
  loadUsers() {
    this._loading.set(true);
    this.http.get<User[]>(this.apiUrl).subscribe({
      next: (users) => {
        this._users.set(users);
        this._loading.set(false);
      },
      error: (err) => {
        this._error.set(err.message);
        this._loading.set(false);
      }
    });
  }
  
  addUser(user: Omit<User, 'id'>) {
    this.http.post<User>(this.apiUrl, user).subscribe({
      next: (newUser) => {
        this._users.update(users => [...users, newUser]);
      },
      error: (err) => {
        this._error.set(err.message);
      }
    });
  }
  
  deleteUser(id: string) {
    this.http.delete(`${this.apiUrl}/${id}`).subscribe({
      next: () => {
        this._users.update(users => 
          users.filter(u => u.id !== id)
        );
      },
      error: (err) => {
        this._error.set(err.message);
      }
    });
  }
}

// user-list.component.ts
import { Component, OnInit, inject, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { UserService } from './user.service';
import { UserCardComponent } from './user-card.component';

@Component({
  selector: 'app-user-list',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, UserCardComponent],
  template: `
    <div class="container">
      <h2>Users (Admin Count: {{ userService.adminCount() }})</h2>
      
      @if (userService.loading()) {
        <div class="spinner">Loading...</div>
      } @else if (userService.error(); as error) {
        <div class="error">{{ error }}</div>
      } @else {
        <div class="user-grid">
          @for (user of userService.users(); track user.id) {
            <app-user-card 
              [user]="user"
              (delete)="onDeleteUser($event)"
            />
          } @empty {
            <p>No users found</p>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    .container {
      max-width: 1200px;
      margin: 0 auto;
      padding: 2rem;
    }
    
    .spinner {
      text-align: center;
      padding: 2rem;
    }
    
    .error {
      background-color: #ffebee;
      color: #c62828;
      padding: 1rem;
      border-radius: 4px;
    }
    
    .user-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 1rem;
    }
  `]
})
export class UserListComponent implements OnInit {
  protected userService = inject(UserService);
  
  ngOnInit() {
    this.userService.loadUsers();
  }
  
  onDeleteUser(userId: string) {
    if (confirm('Are you sure?')) {
      this.userService.deleteUser(userId);
    }
  }
}

// user-card.component.ts
import { Component, input, output, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { User } from './user.service';

@Component({
  selector: 'app-user-card',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    <div class="card" [class.admin]="isAdmin()">
      <h3>{{ user().name }}</h3>
      <p>{{ user().email }}</p>
      <span class="badge">{{ user().role }}</span>
      <div class="actions">
        <button (click)="onDelete()">Delete</button>
      </div>
    </div>
  `,
  styles: [`
    .card {
      border: 1px solid #ddd;
      border-radius: 8px;
      padding: 1rem;
      background: #f9f9f9;
    }
    
    .card.admin {
      border-color: #ff9800;
      background: #fff8f0;
    }
    
    .badge {
      display: inline-block;
      margin-top: 0.5rem;
      padding: 0.25rem 0.5rem;
      background: #2196f3;
      color: white;
      border-radius: 4px;
      font-size: 0.8rem;
    }
    
    .actions {
      margin-top: 1rem;
      display: flex;
      gap: 0.5rem;
    }
    
    button {
      flex: 1;
      padding: 0.5rem;
      background: #f44336;
      color: white;
      border: none;
      border-radius: 4px;
      cursor: pointer;
    }
  `]
})
export class UserCardComponent {
  user = input.required<User>();
  delete = output<string>();
  
  protected isAdmin = computed(() => 
    this.user().role === 'admin'
  );
  
  onDelete() {
    this.delete.emit(this.user().id);
  }
}
```

### Reactive Form with Validation

```typescript
import { Component, inject } from '@angular/core';
import { 
  FormBuilder, 
  ReactiveFormsModule, 
  Validators
} from '@angular/forms';
import { CommonModule } from '@angular/common';

interface ContactForm {
  name: string;
  email: string;
  message: string;
}

@Component({
  selector: 'app-contact',
  standalone: true,
  imports: [ReactiveFormsModule, CommonModule],
  template: `
    <form [formGroup]="form" (ngSubmit)="onSubmit()">
      <div class="form-group">
        <label>Name</label>
        <input formControlName="name" type="text" />
        @if (form.get('name')?.hasError('required')) {
          <span class="error">Name is required</span>
        }
      </div>
      
      <div class="form-group">
        <label>Email</label>
        <input formControlName="email" type="email" />
        @if (form.get('email')?.hasError('email')) {
          <span class="error">Invalid email format</span>
        }
      </div>
      
      <div class="form-group">
        <label>Message</label>
        <textarea formControlName="message"></textarea>
        @if (form.get('message')?.hasError('minlength')) {
          <span class="error">Message must be at least 10 characters</span>
        }
      </div>
      
      <button 
        type="submit" 
        [disabled]="form.invalid || isSubmitting()"
      >
        {{ isSubmitting() ? 'Sending...' : 'Send Message' }}
      </button>
    </form>
  `,
  styles: [`
    form {
      max-width: 500px;
      margin: 0 auto;
    }
    
    .form-group {
      margin-bottom: 1.5rem;
    }
    
    label {
      display: block;
      margin-bottom: 0.5rem;
      font-weight: 500;
    }
    
    input, textarea {
      width: 100%;
      padding: 0.75rem;
      border: 1px solid #ddd;
      border-radius: 4px;
      font-family: inherit;
    }
    
    .error {
      color: #f44336;
      font-size: 0.875rem;
      margin-top: 0.25rem;
      display: block;
    }
    
    button {
      width: 100%;
      padding: 0.75rem;
      background: #2196f3;
      color: white;
      border: none;
      border-radius: 4px;
      cursor: pointer;
    }
    
    button:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
  `]
})
export class ContactComponent {
  private fb = inject(FormBuilder);
  
  isSubmitting = signal(false);
  
  form = this.fb.group<ContactForm>({
    name: ['', [Validators.required, Validators.minLength(3)]],
    email: ['', [Validators.required, Validators.email]],
    message: ['', [Validators.required, Validators.minLength(10)]]
  });
  
  onSubmit() {
    if (this.form.valid) {
      this.isSubmitting.set(true);
      
      // Symuluj API call
      setTimeout(() => {
        const data = this.form.getRawValue();
        console.log('Form submitted:', data);
        this.form.reset();
        this.isSubmitting.set(false);
      }, 2000);
    }
  }
}
```

---

## Podsumowanie - Checklist

✅ **Architektura:**
- [ ] Wszystkie komponenty są standalone
- [ ] Imports są specificzne (nie laziłoading gdzie się da)
- [ ] OnPush change detection wszędzie

✅ **State Management:**
- [ ] Używasz Signals zamiast properties
- [ ] Computed dla derived state
- [ ] Effects dla side effects

✅ **Performance:**
- [ ] @defer dla heavy components
- [ ] Track by functions w @for
- [ ] toSignal zamiast async pipe gdzie możliwe

✅ **Code Quality:**
- [ ] ESLint z prefer-standalone rule
- [ ] Typed reactive forms
- [ ] Lifecycle interfaces implementowane

✅ **RxJS:**
- [ ] takeUntilDestroyed w subscriptions
- [ ] SwitchMap dla request cancellation
- [ ] toSignal/toObservable dla integrations

---

## Pułapki RxJS — forkJoin z pustą tablicą

### Problem: `([] as any)` w forkJoin powoduje TS18046

```typescript
// ❌ ŹLE — TypeScript nie potrafi wydedukować typu
forkJoin({
  data: this.http.get<Item[]>('/api/items'),
  extra: condition ? this.http.get<Item[]>('/api/other') : ([] as any),
})
// Błąd: 'extra' is of type 'unknown'
```

```typescript
// ✅ DOBRZE — użyj of() z typem
import { forkJoin, of } from 'rxjs';

forkJoin({
  data: this.http.get<Item[]>('/api/items'),
  extra: condition
    ? this.http.get<Item[]>('/api/other')
    : of([] as Item[]),
})
```

**Zasada**: Każde ramię `forkJoin` musi być `Observable<T>`. `of([])` tworzy Observable, surowa tablica nie.

---

## Formularz reaktywny — pułapka z `valueChanges` przy `reset()`

Gdy `form.reset()` jest wywoływany przy `openEdit/openCreate`, subskrypcje `valueChanges` na polach formularza strzelają i mogą wywołać niezamierzone efekty uboczne (np. resetowanie pól zależnych).

```typescript
// ❌ ŹLE — reset() triggeruje valueChanges
this.form.reset({ discipline: null, lead_coach: null });

// ✅ DOBRZE — wyłącz emisję zdarzeń przy resecie
this.form.reset(
  { discipline: null, lead_coach: null },
  { emitEvent: false }
);
```

---

## `[(ngModel)]` wymaga `FormsModule`

Komponent standalone używający `[(ngModel)]` (two-way binding) **musi** deklarować `FormsModule` w `imports`:

```typescript
// ✅ DOBRZE
@Component({
  selector: 'app-example',
  imports: [CommonModule, FormsModule, ReactiveFormsModule, ...],
  ...
})
export class ExampleComponent {}
```

**Uwaga**: `ReactiveFormsModule` (dla `formGroup`) i `FormsModule` (dla `ngModel`) to **dwa różne** moduły. Można potrzebować obu jednocześnie — np. gdy główny formularz używa `[formGroup]`, ale modal wewnątrz używa `[(ngModel)]`.

---

## filterForm.valueChanges — natychmiastowe odświeżenie listy

Filtry (select) powinny odświeżać listę bez opóźnienia (inaczej niż wyszukiwarka z debounce):

```typescript
private setupSubscriptions(): void {
  // Wyszukiwarka — z debounce
  this.searchForm.get('search')?.valueChanges
    .pipe(debounceTime(300), distinctUntilChanged(), takeUntil(this.destroy$))
    .subscribe(() => { this.currentPage = 1; this.loadData(); });

  // Filtry (select) — natychmiast
  this.filterForm.valueChanges
    .pipe(takeUntil(this.destroy$))
    .subscribe(() => { this.currentPage = 1; this.loadData(); });
}
```

---

## Emoji jako przyciski — problem z ciemnym motywem

Emoji (👥, ✏️, 🗑️) mogą być **niewidoczne** w ciemnych motywach przeglądarki lub na ciemnych kartach, bo renderują się w kolorze zbliżonym do tła.

```html
<!-- ❌ Ryzyko — niewidoczne w dark mode -->
<button class="btn-icon" (click)="openModal()">👥</button>

<!-- ✅ Bezpieczne — widoczny tekst + opcjonalne emoji -->
<button class="btn btn--ghost-sm" (click)="openModal()">👤 Zawodnicy</button>
```

**Zasada**: dla akcji krytycznych (szczególnie na kartach z ciemnym tle) używaj **przycisków z tekstem**, nie samych emoji.

---

## Wielojęzyczność (i18n) z ngx-translate — MANDATORY

**⚠️ KRYTYCZNE**: W aplikacji wielojęzycznej **ABSOLUTNIE ZABRANIA SIĘ** wpisywania hardcoded tekstów w kodzie lub templatech.

### Konfiguracja ngx-translate

```typescript
// app.config.ts
import { ApplicationConfig } from '@angular/core';
import { provideHttpClient, withFetch } from '@angular/common/http';
import { TranslateModule, TranslateLoader } from '@ngx-translate/core';
import { TranslateHttpLoader } from '@ngx-translate/http-loader';
import { HttpClient } from '@angular/common/http';

export function HttpLoaderFactory(http: HttpClient) {
  return new TranslateHttpLoader(http, './assets/i18n/', '.json');
}

export const appConfig: ApplicationConfig = {
  providers: [
    provideHttpClient(withFetch()),
    importProvidersFrom(
      TranslateModule.forRoot({
        defaultLanguage: 'pl',
        loader: {
          provide: TranslateLoader,
          useFactory: HttpLoaderFactory,
          deps: [HttpClient]
        }
      })
    )
  ]
};
```

### Struktura plików tłumaczeń

```
src/assets/i18n/
├── pl.json    # Polski (domyślny)
├── en.json    # English
└── de.json    # Deutsch
```

**Konwencja nazewnicza kluczy** (hierarchiczna dot notation):

```json
{
  "common": {
    "error": "Wystąpił błąd",
    "success": "Sukces!",
    "buttons": {
      "save": "Zapisz",
      "cancel": "Anuluj",
      "delete": "Usuń"
    },
    "errors": {
      "network": "Błąd połączenia. Sprawdź swoje połączenie internetowe.",
      "failed_load": "Nie udało się załadować danych",
      "failed_save": "Nie udało się zapisać danych",
      "failed_delete": "Nie udało się usunąć",
      "validation": "Sprawdź poprawność wszystkich pól"
    },
    "confirm": {
      "delete_user": "Czy na pewno chcesz usunąć tego użytkownika?",
      "delete_item": "Czy na pewno chcesz usunąć ten element?"
    }
  },
  "pages": {
    "admin": {
      "users": {
        "title": "Użytkownicy",
        "email_exists": "Email już istnieje w bazie",
        "field_required": "To pole jest wymagane",
        "field_min_length": "Minimum {{min}} znaków",
        "field_email_invalid": "Niepoprawny adres email"
      }
    }
  },
  "enums": {
    "status": {
      "active": "Aktywny",
      "suspended": "Zawieszony",
      "deactivated": "Dezaktywowany"
    }
  }
}
```

### Użycie w templatech (HTML)

```html
<!-- ✅ DOBRZE — pipe translate -->
<h1>{{ 'pages.admin.users.title' | translate }}</h1>
<button>{{ 'common.buttons.save' | translate }}</button>

<!-- ✅ Z parametrami -->
<p>{{ 'common.welcome' | translate: {name: userName} }}</p>

<!-- ❌ ŹLE — hardcoded tekst -->
<h1>Użytkownicy</h1>
<button>Zapisz</button>
```

### Użycie w TypeScript (komponent)

```typescript
import { TranslateService } from '@ngx-translate/core';

@Component({
  selector: 'app-users-list',
  imports: [CommonModule, TranslateModule],
  templateUrl: './users-list.component.html'
})
export class UsersListComponent {
  constructor(private translate: TranslateService) {}

  deleteUser(userId: number): void {
    // ✅ DOBRZE — tłumaczenie confirm message
    const confirmMsg = this.translate.instant('common.confirm.delete_user');
    if (confirm(confirmMsg)) {
      this.http.delete(`/api/users/${userId}/`)
        .subscribe({
          error: (err) => {
            // ✅ DOBRZE — tłumaczenie error message
            this.error = this.translate.instant('common.errors.failed_delete');
          }
        });
    }
  }

  getStatusLabel(status: string): string {
    // ✅ DOBRZE — dynamiczny klucz enum
    const key = `enums.status.${status}`;
    const translated = this.translate.instant(key);
    return translated !== key ? translated : status;
  }
}
```

### Erro messages z walidacji formularzy

```typescript
getErrorMessage(fieldName: string): string {
  const field = this.form.get(fieldName);
  if (!field || !field.errors || !field.touched) {
    return '';
  }
  
  // ✅ DOBRZE — wszystkie komunikaty przez translate
  if (field.errors['required']) {
    return this.translate.instant('pages.admin.users.field_required');
  }
  if (field.errors['minlength']) {
    const minLen = field.errors['minlength']?.requiredLength || 0;
    return this.translate.instant('pages.admin.users.field_min_length', { min: minLen });
  }
  if (field.errors['email']) {
    return this.translate.instant('pages.admin.users.field_email_invalid');
  }
  
  return '';
}
```

### Zwracanie kluczy zamiast tekstów (dla wyświetlenia w template)

Gdy error message jest wyświetlany w template przez `{{ error | translate }}`, komponent może zwrócić **klucz i18n zamiast przetłumaczonego tekstu**:

```typescript
// Component TypeScript
private getErrorMessage(error: any): string {
  if (error?.status === 400) {
    return 'auth.confirm.error_invalid';  // ✅ Klucz, nie tekst
  }
  if (error?.status === 429) {
    return 'auth.errors.rate_limit';  // ✅ Klucz
  }
  if (error?.status === 0) {
    return 'common.errors.network';  // ✅ Klucz
  }
  return 'common.errors.failed_load';  // ✅ Klucz
}
```

```html
<!-- Template HTML -->
<div class="alert alert-error">
  <span>{{ error | translate }}</span>  <!-- Translate pipe przetłumaczy klucz -->
</div>
```

### Checklist dla wielojęzyczności

Przed commitem ZAWSZE sprawdź:

- [ ] Żadnych hardcoded tekstów w `.ts` (szczególnie `this.error = 'Tekst'`)
- [ ] Żadnych hardcoded tekstów w `.html` (wszystko przez `| translate`)
- [ ] Żadnych hardcoded tekstów w `confirm()`, `alert()` (użyj `this.translate.instant()`)
- [ ] Wszystkie klucze istnieją we WSZYSTKICH językach (PL, EN, DE)
- [ ] Parametry używane poprawnie (np. `{{min}}`, `{{name}}`)
- [ ] Enum labels przez dynamiczne klucze (`enums.status.${status}`)

**Zasada zerowej tolerancji**: Jeśli użytkownik niemieckojęzyczny zobaczy polski tekst — to **BUG P0** (krytyczny).

---

## API Response Handling - Type Conversion Pattern

**Problem**: Backend API may return nested objects (e.g., `roles: [{name: 'ADMIN', disciplines: []}]`), but form controls expect primitive types (e.g., `['ADMIN']`). This causes ng-select validation warnings.

**Solution**: Implement explicit type conversion functions and use `emitEvent: false` when patching values.

### Pattern: Converting API Objects to Form Primitives

```typescript
// user-form.component.ts
import { Component, inject, OnInit } from '@angular/core';
import { FormBuilder, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { TranslateModule } from '@ngx-translate/core';
import { HttpClient } from '@angular/common/http';
import { CommonModule } from '@angular/common';
import { NgSelectModule } from '@ng-select/ng-select';

interface RoleWithDisciplines {
  name: string;
  disciplines: Array<{
    id: number;
    name: string;
    category: string;
    is_individual: boolean;
  }>;
}

interface PlayerFormData {
  id: number;
  first_name: string;
  last_name: string;
  email: string;
  roles: (string | RoleWithDisciplines)[]; // API returns objects, we'll convert
}

@Component({
  selector: 'app-user-form',
  standalone: true,
  imports: [ReactiveFormsModule, CommonModule, NgSelectModule, TranslateModule],
  template: `
    <form [formGroup]="form" (ngSubmit)="onSubmit()">
      <!-- Roles dropdown - expects string array -->
      <ng-select
        formControlName="roles"
        [items]="availableRoles"
        bindLabel="name"
        bindValue="name"
        placeholder="Select roles"
        multiple
      ></ng-select>
      <button type="submit">Save</button>
    </form>
  `
})
export class UserFormComponent implements OnInit {
  private http = inject(HttpClient);
  private fb = inject(FormBuilder);
  
  form = this.fb.group({
    id: [0],
    first_name: [''],
    last_name: [''],
    email: [''],
    roles: [[] as string[]], // Form expects string array
  });
  
  availableRoles = [
    { name: 'ADMIN' },
    { name: 'COACH' },
    { name: 'PLAYER' },
    { name: 'PARENT' }
  ];
  
  ngOnInit() {
    this.loadPlayer(50);
  }
  
  /**
   * Load user from API and handle type conversion
   * API returns: roles: [{name: 'ADMIN', disciplines: []}]
   * Form expects: roles: ['ADMIN', 'COACH']
   */
  loadPlayer(userId: number) {
    this.http.get<PlayerFormData>(`/api/users/${userId}/`).subscribe(
      data => {
        // ✅ Extract role names from API objects
        // Handle both cases: string (already converted) or RoleWithDisciplines object
        const roleNames = data.roles.map(role => 
          typeof role === 'string' ? role : (role?.name || '')
        );
        
        // Set form with converted values
        // ⚠️ IMPORTANT: Use emitEvent: false to prevent valueChanges trigger
        this.form.patchValue({
          id: data.id,
          first_name: data.first_name,
          last_name: data.last_name,
          email: data.email,
          roles: roleNames // Form gets clean string array
        }, { emitEvent: false });
      }
    );
  }
  
  /**
   * Helper: safely extract role name from mixed types
   * Handles backward compatibility: string | RoleWithDisciplines
   */
  getTypedRoleName(role: string | RoleWithDisciplines): string {
    return typeof role === 'string' ? role : (role?.name || '');
  }
  
  /**
   * Helper: safely extract disciplines from mixed role types
   */
  getTypedDisciplines(role: string | RoleWithDisciplines): Array<any> {
    return typeof role === 'string' ? [] : (role?.disciplines || []);
  }
}
```

### Key Points

**Type Conversion**:
- ✅ Use type guards: `typeof role === 'string'` to handle union types
- ✅ Convert API objects to primitives **before** `patchValue()` on form controls
- ✅ Keep form control types consistent (don't mix string and object)

**Form Control Updates**:
- ✅ Use `emitEvent: false` when patching initial data
  - Prevents `valueChanges` subscription from triggering during form initialization
  - Avoids clearing fields or triggering unnecessary API calls
- ❌ Never use `setValue()` for partial updates — use `patchValue()` instead

**ng-select Compatibility**:
```typescript
// ❌ WRONG: Setting object as form value causes warning
form.patchValue({ roles: [{name: 'ADMIN', disciplines: []}] });

// ✅ CORRECT: Extract primitive value beforehand
const roleNames = roles.map(r => r.name);
form.patchValue({ roles: roleNames });
```

**Template Iteration with Original Objects**:

```html
<!-- Template can still work with original objects for display -->
<div *ngFor="let role of player.roles">
  <h3>{{ getTypedRoleName(role) }}</h3>
  <div *ngFor="let discipline of getTypedDisciplines(role)">
    {{ discipline.name }}
  </div>
</div>
```

---

## CSS Optimization - Budget Management

**Context**: Esbuild monitors component CSS bundle size with warnings (>6 kB) and errors (>10 kB) to ensure optimal performance.

### Consolidating Style Variants

**Pattern**: Merge similar states/roles using scss grouping instead of creating separate classes.

#### ❌ Wrong (Bloated):
```scss
/* Each role gets unique colors = duplication */
.badge {
  &.role-player {
    background: rgba(59, 130, 246, 0.15);
    color: #1e40af;
  }
  
  &.role-parent {
    background: rgba(59, 130, 246, 0.10);  // Slightly different shade
    color: #1e40af;
  }
  
  &.role-coach {
    background: rgba(236, 72, 153, 0.15);
    color: #831843;
  }
  
  &.role-team-manager {
    background: rgba(139, 92, 246, 0.15);  // Similar to coach
    color: #4c1d95;
  }
  
  &.role-admin {
    background: rgba(239, 68, 68, 0.15);
    color: #991b1b;
  }
  
  &.role-super-admin {
    background: rgba(124, 45, 18, 0.15);  // Custom color no other role uses
    color: #7c2d12;
  }
}
```
**Result**: 6 separate declarations × 2 properties = ~120 bytes of duplication

#### ✅ Correct (Consolidated - saves ~50%):
```scss
.badge {
  /* Group visual-similar roles */
  &.role-player,
  &.role-parent {
    background: rgba(59, 130, 246, 0.15);
    color: #1e40af;
  }
  
  &.role-coach,
  &.role-team-manager {
    background: rgba(236, 72, 153, 0.15);
    color: #831843;
  }
  
  &.role-admin,
  &.role-super-admin {
    background: rgba(239, 68, 68, 0.15);
    color: #991b1b;
  }
  
  &.role-unknown {
    background: var(--color-gray-200);
    color: var(--color-gray-700);
  }
}
```
**Result**: 4 declarations with same color = ~60 bytes total

### Removing Decorative Styles

**Pattern**: Delete animations, transitions, and hover effects that don't add value.

#### Remove these first (lowest impact on UX):
```scss
// ❌ Unnecessary animations
@keyframes ctx-appear {
  from { opacity: 0; transform: scale(0.97) translateY(-3px); }
  to   { opacity: 1; transform: scale(1)   translateY(0); }
}

// ❌ Decorative transitions
&:hover {
  background-color: var(--color-gray-100);
  color: var(--color-gray-700);
  transition: background-color 0.15s, color 0.15s;  // 70+ bytes for smooth hover
}

// ❌ Visual decorations on empty states
.empty-state {
  border: 2px dashed var(--color-gray-300);  // Unnecessary border saves ~20 bytes
  padding: var(--space-12);
}

// ❌ Box shadows (common culprit - adds ~80-100 bytes each)
box-shadow: 0 4px 6px -1px rgba(0,0,0,.1), 0 10px 25px -5px rgba(0,0,0,.12);
```

### Removing Redundant/Unused Blocks

**Pattern**: Check for dead code (styles that don't match any template element).

```scss
/* ❌ Modal styles for old form component that was deleted */
.modal-overlay { ... }
.modal-content { ... }
.disc-loading { ... }
.disc-list { ... }

/* ❌ Alternative badge colors that aren't used */
&.status-unknown { background-color: var(--color-gray-200); }
&.role-unknown { background-color: var(--color-gray-200); }

/* ✅ Use CSS custom properties instead of hardcoding */
/* Before: 5 different shades of gray scattered in code */
/* After: Single custom property used consistently */
--color-gray-50, --color-gray-100, --color-gray-200
```

### Budget Thresholds & Process

| Threshold | Action |
|-----------|--------|
| < 6 kB | ✅ OK, green light |
| 6-10 kB | ⚠️ WARNING logged, should optimize |
| > 10 kB | 🔴 ERROR, build fails |

**Optimization Process** (iterative):
1. Run `npm run build` - note current size
2. Identify largest blocks (use browser DevTools scss inspector)
3. Apply consolidation (merge variants)
4. Remove decorative styles (lowest hanging fruit)
5. Remove unused blocks (grep check)
6. Rerun build, verify reduction

**Example from users-list.component.scss**:
```
Initial: 10.44 kB (444 bytes over limit)
├─ Remove unused modal/discipline picker styles: 10.44 → 9.20 kB
├─ Consolidate 6 role badges → 3 merged: 9.20 → 8.50 kB
├─ Remove context menu animation: 8.50 → 8.20 kB
├─ Remove box-shadow, transitions, hover: 8.20 → 7.50 kB
└─ Final micro-trims (min-width, line-height): 7.50 → 6.00 kB ✅

Total reduction: 40.2% (saved 444 bytes and went from ERROR to OK status)
```

### Best Practices

- ✅ Use SCSS grouping selector: `&.class1, &.class2 { ... }` to share declarations
- ✅ Prefer CSS custom properties `var(--color-primary)` over hardcoding colors
- ✅ Consolidate at design time - don't repeat color values across variants
- ✅ Plan variant consolidation when adding new states (don't create new color per state)
- ❌ Don't add animations unless they enhance user experience
- ❌ Don't use `transition` on hover for simple state changes
- ❌ Don't hardcode colors multiple times - use design tokens

### Monitoring (Esbuild)

Esbuild automatically warns during build:
```bash
$ npm run build
…
✘ [ERROR] src/app/pages/admin/users/users-list.component.scss exceeded maximum budget.
Budget 10.00 kB was not met by 444 bytes (size: 10.44 kB)
```

Fix trigger:
```bash
$ npm run build 2>&1 | grep "exceeded\|budget"  # Identify overages
# Then apply consolidation + removal patterns above
```

---

## Zasoby

- **Oficjalna dokumentacja**: https://angular.dev
- **Angular ESLint**: https://angular-eslint.io
- **Style Guide**: https://angular.dev/style-guide
- **Best Practices Performance**: https://angular.dev/best-practices/runtime-performance

---

**Ostatnia aktualizacja**: 26 luty 2026  
**Wersja Angular**: 21.2.0+sha-661e800

