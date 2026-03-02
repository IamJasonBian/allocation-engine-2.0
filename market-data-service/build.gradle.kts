plugins {
    scala
    application
}

repositories {
    mavenCentral()
}

val scala3Version = "3.3.4"

dependencies {
    implementation("org.scala-lang:scala3-library_3:$scala3Version")

    // HTTP client
    implementation("com.softwaremill.sttp.client3:core_3:3.9.7")

    // JSON
    implementation("io.circe:circe-core_3:0.14.9")
    implementation("io.circe:circe-generic_3:0.14.9")
    implementation("io.circe:circe-parser_3:0.14.9")

    // Redis
    implementation("redis.clients:jedis:5.1.3")

    // Logging
    implementation("org.slf4j:slf4j-simple:2.0.13")

    // Test
    testImplementation("org.scalatest:scalatest_3:3.2.19")
}

application {
    mainClass.set("com.helsinki.marketdata.run")
}

tasks.withType<ScalaCompile> {
    scalaCompileOptions.additionalParameters = listOf("-feature", "-deprecation")
}
