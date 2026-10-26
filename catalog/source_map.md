# Source map: cross-system links discovered

```mermaid
flowchart LR
  subgraph postgres["postgres"]
    rental_core__actor["actor"]
    rental_core__address["address"]
    rental_core__category["category"]
    rental_core__city["city"]
    rental_core__country["country"]
    rental_core__customer["customer"]
    rental_core__film["film"]
    rental_core__film_actor["film_actor"]
    rental_core__film_category["film_category"]
    rental_core__film_embedding["film_embedding"]
    rental_core__inventory["inventory"]
    rental_core__language["language"]
    rental_core__payment["payment"]
    rental_core__rental["rental"]
    rental_core__staff["staff"]
    rental_core__store["store"]
  end
  subgraph mongodb["mongodb"]
    customer_app__customer_profiles["customer_profiles"]
    customer_app__film_catalog["film_catalog"]
  end
  subgraph json_files["json files"]
    partner_feeds__payments["payments"]
  end
  customer_app__customer_profiles -->|customer_id| rental_core__customer
  customer_app__customer_profiles -->|store_id| rental_core__store
  customer_app__customer_profiles -->|contact.address.country| rental_core__country
  customer_app__customer_profiles -->|recent_rentals[].rental_id| rental_core__rental
  customer_app__customer_profiles -->|recent_rentals[].film_id| rental_core__film
  customer_app__film_catalog -->|film_id| rental_core__film
  customer_app__film_catalog -->|cast[].actor_id| rental_core__actor
  partner_feeds__payments -->|customerRef| rental_core__customer
  partner_feeds__payments -->|rentalRef| rental_core__rental
```